#!/usr/bin/python2

# ^^^ Was trying to use python3, but protobuffers are having packaging problems.

# This script has 4 steps:
# 1. Invoke Undertow to perform a migration
# 2. Monitor kafka for completion
# 3. Query elasticsearch for the results
#
# It throws an exit code there was a failure.


from sys import path, argv
from time import sleep
from kafka import KafkaConsumer
from ktest_pb2 import LegacyEventUpdate 
from datetime import datetime
from time import time as timest
from elasticsearch import Elasticsearch, RequestsHttpConnection
import logging
import fnmatch
import os
import pwd
import pexpect
import json
import httplib
import iso8601
import logging
import argparse
import mysql.connector
import json

#es_logger = logging.getLogger('elasticsearch')
#es_logger.setLevel(logging.DEBUG)
#consoleHandler = logging.StreamHandler()
#es_logger.addHandler(consoleHandler)
#httplib.HTTPConnection.debuglevel = 3
#httplib.HTTPSConnection.debuglevel = 3

def make_query(when, event_id) : 
    now = timest() * 1000

    query = "(event_type: live_migrate OR event_type: live-migration) AND (event_id: " + str(event_id) + " OR event_id: \"" + str(event_id) + "\")"

    print "Running query: " + query

    return json.dumps(
        {
          "query": {
            "filtered": {
                "query": {
                    "query_string": {
                        "analyze_wildcard": True,
                        "query": query
                    }
                },
                "filter": {
                    "bool": {
                        "must": [
                            { "range": { 
                                "@timestamp": { 
                                    "gt" : "now-" + when
                                } 
                               }
                            }
                        ]
                    }
                }
            }
         }
         , "size":  1000, "sort":  [{"@timestamp":{"order":"desc","unmapped_type":"boolean"}}],
       }
    )

def by_time( a ):
  return iso8601.parse_date(a["_source"]["@timestamp"])

parser = argparse.ArgumentParser(description='Single-shot migration and monitoring.')
parser.add_argument('--eshost', type=str, default="elasticsearch.internal.digitalocean.com", help='ElasticSearch hostname')
parser.add_argument('--esscheme', type=str, default="http", help='ElasticSearch scheme: https/http')
parser.add_argument('--esauth', type=str, default="", help='ElasticSearch HTTP authentication, default: Not used')
parser.add_argument('--esport', type=int, default=80, help='ElasticSearch port')
parser.add_argument('--esrange', type=str, default='1d', help='ElastiCsearch default query range.')
parser.add_argument('--email', type=str, default='', help='Undertow Oauth username to override oauth')
parser.add_argument('--targethv', type=str, required = True, help='Name of the target hypervisor')
parser.add_argument('--droplet', type=int, required = True, help='ID# of the droplet')
parser.add_argument('--slavepass', type=str, required = True, help='MySQL read-only slave password')
parser.add_argument('--output', type=str, default='/tmp/migrations.json', help = 'Path to output JSON file')
parser.add_argument('--nokafka', action='store_true', default=False, help = 'Use Alpha instead of Kafka to check for completion.')

mysql_hostname = "prod-mysql1d.nyc2.internal.digitalocean.com"
#mysql_hostname = "localhost"
mysql_username = "DO-read-only"
mysql_database = "digitalocean"
args = parser.parse_args()

try :
    cnx = mysql.connector.connect(user = mysql_username, password = args.slavepass, host = mysql_hostname, database = mysql_database)
except mysql.connector.Error as err :
    if err.errno == mysql.connector.errorcode.ER_ACCESS_DENIED_ERROR:
        error("Something is wrong with your user name or password")
    elif err.errno == mysql.connector.errorcode.ER_BAD_DB_ERROR:
        error("Database does not exist")
    else:
        error(err)
    exit(1)

print "Looking up target HV: " + str(args.targethv)
cursor = cnx.cursor()
cursor.execute("select id from servers where name = '" + str(args.targethv) + "'")
targetid = False
targetsize = False

for (row) in cursor :
    targetid = row[0]
    print "Found ID " + str(targetid) + " for HV " + args.targethv
    break

cursor.close()
cnx.close()
cnx = mysql.connector.connect(user = mysql_username, password = args.slavepass, host = mysql_hostname, database = mysql_database)

if not targetid :
    print "Failed to lookup ID for target HV: " + str(args.targethv)
    exit(1)
    

print "Looking up droplet: " + str(args.droplet)
cursor = cnx.cursor()
cursor.execute("select droplets.server_id, sizes.name from droplets left join sizes on sizes.id = droplets.size_id where droplets.id = " + str(args.droplet))
currid = False
for (row) in cursor :
    currid = row[0]
    targetsize = row[1]

    print "Droplet size: " + targetsize

    if str(currid) == str(targetid) :
        print "Droplet-" + str(args.droplet) + " already at target: " + str(currid) + ". Exiting cleanly."
        exit(0)
    else :
        print "Droplet going from " + str(currid) + " to " + str(targetid)

cursor.close()
cnx.close()
cnx = mysql.connector.connect(user = mysql_username, password = args.slavepass, host = mysql_hostname, database = mysql_database)

es = Elasticsearch([dict(host = args.eshost, port = args.esport, http_auth = args.esauth if args.esauth != "" else None, use_ssl=True if args.esscheme == 'https' else False, verify_certs = True if args.esscheme == 'https' else False)], connection_class = RequestsHttpConnection)

try :
    d = {}

    for key in ["UNDERTOW_DB_USER", "UNDERTOW_DB_PASSWORD", "UNDERTOW_DB_ADDR", "UNDERTOW_DB_NAME"] :
        if key in os.environ :
            d[key] = os.environ[key]

    d = {"PATH" : "/usr/bin"}

    cmd = "/home/mhines/undertow live-migrate "

    if args.email != "" :
        cmd += "--email=" + args.email + " "

    cmd += "--target-server-id=" + str(targetid) + " --droplet-id=" + str(args.droplet)

    print("Starting undertow: " + cmd)

    child = pexpect.spawn(cmd)

    while True :
        if args.email == "" :
            result = child.expect([".*cloud.digitalocean.com.*", ".*Cannot wait for dead child process.*", pexpect.EOF])
            if result != 0 :
                print("Child died. Will try again: " + str(result))
                sleep(10)
                continue

            print(child.after)
            result = child.expect([".*verified caller credentials.*", ".*Cannot wait for dead child process.*", pexpect.EOF])
            if result == 1 :
                print("OAUTH FAILED!")
                api.appdetach(_cloud_name, ai["uuid"])
                exit(1)
            elif result == 2 :
                print("Child died. Will try again: " + str(result))
                sleep(10)
                continue
        break

    result = child.expect([".*event_id.*", pexpect.EOF])
    if result == 1 :
        print("MIGRATION FAILED: " + str(result))
        print(child.before)
        print(child.after)
        api.appdetach(_cloud_name, ai["uuid"])
        exit(1)

    ejson = json.loads(child.after) 
    child.wait()

    consumer = KafkaConsumer('legacy-event-updates', bootstrap_servers=['prod-eventstatus-kb-1.nyc2.internal.digitalocean.com:9092', 'prod-eventstatus-kb-2.nyc2.internal.digitalocean.com:9092', 'prod-eventstatus-kb-3.nyc2.internal.digitalocean.com:9092'])
   
    event_id = ejson["event_id"]
    print("Waiting for event_id: " + str(event_id))

    if not args.nokafka :
        for message in consumer:
            event = LegacyEventUpdate()
            event.ParseFromString(message.value)
            if event.event_id == event_id :
                if event.action_status == event.IN_PROGRESS :
                    print("Migration in progress: " + str(event.percentage) + "%")
                else :
                    if event.action_status == event.DONE :
                        print("Migration complete!")
                    elif event.action_status == event.ERROR :
                        print("Migration failed: " + event.description)
                        exit(1)
                    break
    else :
        print("Skipping kafka. Waiting for current HV id " + str(currid) + " to become " + str(targetid) + "...")
        done = False
        while not done :
            cursor = cnx.cursor()
            cursor.execute("select droplets.server_id, sizes.name from droplets left join sizes on sizes.id = droplets.size_id where droplets.id = " + str(args.droplet))
            lastid = False
            for (row) in cursor :
                lastid = row[0]
                targetsize = row[1]

                print "Droplet current HV id: " + str(lastid)

                if str(currid) != str(lastid) :
                    print "Droplet-" + str(args.droplet) + " now at target: " + str(lastid) + ". Migration complete."
                    done = True
                else :
                    print("Migration in progress...")
                    sleep(5)
                break
            cursor.close()
            cnx.close()
            cnx = mysql.connector.connect(user = mysql_username, password = args.slavepass, host = mysql_hostname, database = mysql_database)

    downtime_found = False

    check_limit = 5

    data = {"memory_iterations" : [], "block_iterations" : []}

    while not downtime_found and check_limit > 0 :
        print("Waiting a little before query...")
        sleep(10)
        result = es.search(index="backend-*", body = make_query(args.esrange, event_id))
        print("Query complete.")

        if result["hits"]["total"] > 0 :
            results = result["hits"]["hits"]
            results.sort(key = by_time)
            for result in results :
                if "status" in result["_source"] :
                    extract = {"timestamp" : result["_source"]["@timestamp"]}
                    extract.update(result["_source"]["status"])
                    if "io-status" in extract :
                        data["block_iterations"].append(extract)
                    else :
                        data["memory_iterations"].append(extract)

                if "domjobinfo" in result["_source"] and "downtime" in result["_source"]["domjobinfo"] :
                    downtime_found = True
                    data["final"] = result["_source"]["domjobinfo"]

        else :
            print(" no data. =(")

        check_limit -= 1

    if "final" in data :
        miter = len(data["memory_iterations"])
        mbps = data["memory_iterations"][-1]["ram"]["mbps"] if miter else -1
        block_length = data["block_iterations"][-1]["len"]
        block_start = iso8601.parse_date(data["block_iterations"][0]["timestamp"])
        block_stop = iso8601.parse_date(data["block_iterations"][-1]["timestamp"])
        block_seconds = (block_stop - block_start).total_seconds()

        data["final"]["block_mbps"] = block_length / block_seconds / 1024 / 1024
        data["final"]["memory_mbps"] = mbps 

        print("Memory iterations: " + str(miter))
        print("Memory Mb/s: " + str(mbps))
        print("Block iterations: " + str(len(data["block_iterations"])))
        print("Block Mb/s: " + str(data["final"]["block_mbps"]))
        print("final: " + str(data["final"]))

        fh = open(args.output, 'a')
        fh.write(json.dumps(data) + "\n")
        fh.close()


    exit(0)

except KeyboardInterrupt :
    print("Aborting this Migration.")
    exit(1)

except Exception as msg :
    print("Problem during migration: " + str(msg))
    exit(1)
