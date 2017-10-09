#!/usr/bin/env python

# This script has 4 steps:
# 1. Invoke CloudBench to fire off a droplet to a target hypervisor and wait for provisioning to be completed
# 2. Invoke Undertow to perform a migration
# 3. Monitor kafka for completion
# 4. Query elasticsearch for the results
# 5. Destroy the droplet
#
# Now that we have this as a formal CloudBench API client, we can perform this operation in a loop and record all the data


from sys import path
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
parser.add_argument('--targethv', type=int, required = True, help='ID# of the target hypervisor')
parser.add_argument('--cloud_name', type=str, required = True, help='CloudBench name of attached cloud')
parser.add_argument('--factor', type=float, required = True, help='Scaling factor of the size of the droplet.')

args = parser.parse_args()

home = os.environ["HOME"]
username = pwd.getpwuid(os.getuid())[0]

api_file_name = "/tmp/cb_api_" + username
if os.access(api_file_name, os.F_OK) :    
    try :
        _fd = open(api_file_name, 'r')
        _api_conn_info = _fd.read()
        _fd.close()
    except :
        _msg = "Unable to open file containing API connection information "
        _msg += "(" + api_file_name + ")."
        print _msg
        exit(4)
else :
    _msg = "Unable to locate file containing API connection information "
    _msg += "(" + api_file_name + ")."
    print _msg
    exit(4)

_path_set = False

for _path, _dirs, _files in os.walk(os.path.abspath(path[0] + "/../")):
    for _filename in fnmatch.filter(_files, "code_instrumentation.py") :
        if _path.count("/lib/auxiliary") :
            path.append(_path.replace("/lib/auxiliary",''))
            _path_set = True
            break
    if _path_set :
        break

from lib.api.api_service_client import *

_msg = "Connecting to API daemon (" + _api_conn_info + ")..."
print _msg
api = APIClient(_api_conn_info)

#---------------------------------- END CB API ---------------------------------

_cloud_name = args.cloud_name

es = Elasticsearch([dict(host = args.eshost, port = args.esport, http_auth = args.esauth if args.esauth != "" else None, use_ssl=True if args.esscheme == 'https' else False, verify_certs = True if args.esscheme == 'https' else False)], connection_class = RequestsHttpConnection)


try :
    error = False

    _cloud_attached = False
    for _cloud in api.cldlist() :
        if _cloud["name"] == _cloud_name :
            _cloud_attached = True
            _cloud_model = _cloud["model"]
            break

    if not _cloud_attached :
        print "Cloud " + _cloud_name + " not attached"
        exit(1)

    d = {}

    btest_threads = max(1, int(args.factor))
    btest_filesize = int(args.factor * 1000)
    ram = int(args.factor * 1024)
    unit = "mb"
    if ram >= 1024 :
        unit = "gb"
        ram /= 1024

    aivalue = "4096;32;50;100;" + str(btest_filesize) + ";" + str(btest_threads)

    print "Specifying AI parameters: filesize: " + str(btest_filesize) + ", threads: " + str(btest_threads) + ", value: " + aivalue

    api.cldalter(_cloud_name, "ai_templates", "btest_load_profile", aivalue)

    vmvalue = "size:" + str(ram) + unit + ",imageids:1,imageid1:16082940,cloudinit_packages:openvpn;netperf;iperf"

    print "Specifying VM parameters: " + str(ram) + unit + ", value: " + vmvalue

    api.cldalter(_cloud_name, "vm_templates", "tinyvm", vmvalue)

    print "Launching application instance..."

    ai = api.appattach(_cloud_name, "btest", temp_attr_list = "tinyvm_pref_pool=sfo2node28")

    aiattrs = api.appshow(_cloud_name, ai["uuid"])
    droplet_id = None
    for vmlabels in aiattrs["vms"].split(",") :
        vmuuid = vmlabels.split("|")[0]
        vm = api.vmshow(_cloud_name, vmuuid)
        droplet_id = int(vm["host_name"])
        print "Droplet ready: " + str(droplet_id)

    for key in ["UNDERTOW_DB_USER", "UNDERTOW_DB_PASSWORD", "UNDERTOW_DB_ADDR", "UNDERTOW_DB_NAME"] :
        if key in os.environ :
            d[key] = os.environ[key]
    d = {"PATH" : "/usr/bin"}

    cmd = "/home/mhines/undertow live-migrate "

    if args.email != "" :
        cmd += "--email=" + args.email + " "

    cmd += "--target-server-id=" + str(args.targethv) + " --droplet-id=" + str(droplet_id)

    print "Starting undertow: " + cmd

    child = pexpect.spawn(cmd)

    if args.email == "" :
        result = child.expect(".*cloud.digitalocean.com.*")
        print(child.after)
        result = child.expect([".*verified caller credentials.*", pexpect.EOF])
        if result == 1 :
            print("OAUTH FAILED!")
            api.appdetach(_cloud_name, ai["uuid"])
            exit(1)

    result = child.expect([".*event_id.*", pexpect.EOF])
    if result == 1 :
        print "MIGRATION FAILED: " + str(result)
        print child.before
        print child.after
        api.appdetach(_cloud_name, ai["uuid"])
        exit(1)

    ejson = json.loads(child.after) 
    child.wait()

    consumer = KafkaConsumer('legacy-event-updates', bootstrap_servers=['prod-eventstatus-kb-1.nyc2.internal.digitalocean.com:9092', 'prod-eventstatus-kb-2.nyc2.internal.digitalocean.com:9092', 'prod-eventstatus-kb-3.nyc2.internal.digitalocean.com:9092'])
   
    event_id = ejson["event_id"]
    print "Waiting for event_id: " + str(event_id)
    for message in consumer:
        event = LegacyEventUpdate()
        event.ParseFromString(message.value)
        if event.event_id == event_id :
            if event.action_status == event.IN_PROGRESS :
                print "Migration in progress: " + str(event.percentage) + "%"
            else :
                if event.action_status == event.DONE :
                    print "Migration complete!"
                elif event.action_status == event.ERROR :
                    print "Migration failed: " + event.description;
                break

    downtime_found = False

    while not downtime_found :
        print("Waiting a little before query...")
        sleep(10)
        result = es.search(index="backend-*", body = make_query(args.esrange, event_id))
        print("Query complete.")

        if result["hits"]["total"] > 0 :
            results = result["hits"]["hits"]
            results.sort(key = by_time)
            for result in results :
                print("Droplet: " + str(result["_source"]["event_id"]) + " Status: " + (str(result["_source"]["status"]) if "status" in result["_source"] else "none") +  " Domjobinfo: " + (str(result["_source"]["domjobinfo"]) if "domjobinfo" in result["_source"] else "none"))

                if "domjobinfo" in result["_source"] and "downtime" in result["_source"]["domjobinfo"] :
                    downtime_found = True

        else :
            print(" no data. =(")
            break

    api.appdetach(_cloud_name, ai["uuid"])

except APIException, obj :
    error = True
    print "API Problem (" + str(obj.status) + "): " + obj.msg

except APINoSuchMetricException, obj :
    error = True
    print "API Problem (" + str(obj.status) + "): " + obj.msg

except KeyboardInterrupt :
    print "Aborting this VM."

except Exception, msg :
    error = True
    print "Problem during experiment: " + str(msg)
    api.appdetach(_cloud_name, ai["uuid"])
