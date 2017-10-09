#!/usr/bin/env python

# Dependencies:
# $ sudo apt-get install python-mysql.connector
# $ sudo apt-get install python-dnspython
# Some of the prometheus servers are shared per region (six of them as of January 2016), 
# but some of them are not. So, we'll poll DNS to figure it out and make another decision later.

# If the user requests a single-node query (using the last parameter on the command line),
# we have to try all the shards programatically to see which one returns a result. For single-node queries,
# it's not a big deal, but the proxy has a bug and doesn't relay this API request correctly: 9090/api/v1/query?query

# Results are printed out in JSON to stdout. You'll want to redirect to a file and then load the JSON.
# Messages are printed to stderr

from __future__ import print_function
import sys
import requests
import dns.resolver                                                                                                    
import mysql.connector
import os
from time import time, sleep
from re import compile as re_compile, IGNORECASE as re_IGNORECASE, sub as re_sub
from json import loads, dumps
from multiprocessing.pool import ThreadPool
from datetime import datetime

def msg(*objs) :
    print(*objs, file=sys.stdout)

def error(*objs) :
    print(*objs, file=sys.stderr)

if len(sys.argv) < 4 :
    error("Usage: ./deliver.py [mysql slave password] [age, eg: 1s, 2m, 3h, 4d, 5w, 6M, 7y] [query string, e.g: \"node_memory_SwapTotal{instance=~'NODE.SLUG.internal.digitalocean.com:9100',job='hypervisors',region='SLUG'}/1024/1024/1024-node_memory_SwapFree{instance=~'NODE.SLUG.internal.digitalocean.com:9100',job='hypervisors',region='SLUG'}/1024/1024/1024\"] [optional: node name, eg. nyc3node500, otherwise the whole fleet]")
    error("NOTE: the keywords 'NODE' and 'SLUG' will be filled in for you automatically")
    exit(1)

def get_srv(domain) :                                                                                                  
    try :
        answers = dns.resolver.query(domain, 'SRV')                                                                    
        urls = []
        for rdata in answers :                                                                                         
            urls.append("http://" + str(rdata.target)[:-1] + ":" + str(rdata.port) + "/")
        return urls
    except dns.resolver.NXDOMAIN, e :                                                                                  
        pass

    return False    

def print_result(node, result) :
    msg("    \"" + node + "\" : " + result + ",")

access_points = {}
ages = {}

def get_results(node) :
    emptyskipped = {}
    youngskipped = {}
    total = 0

    if len(node) > 4 and node[:4] not in access_points :
        #print_result(node, "false")
        return

    if len(node) == 4 :
        if node not in access_points :
            print_result(node, "false")
            return
        wanted_apis = access_points[node]
        node = node if node != "nyc1" else "nbg1"
        wanted_slug = node 
        node += "node.*"
    else :
        wanted_slug = node[:4]
        wanted_apis = access_points[wanted_slug]


    for api in wanted_apis :
        # Now, query prometheus directly:
    #    msg("Requesting data from API: " + api)

        query = sys.argv[3].replace("SLUG", wanted_slug).replace("NODE", node)
        url = api + "api/v1/query?query=" + query + "&time=" + str(start)

        error("URL: " + url)
        attempts = 5
        success = False 
        while attempts > 0 :
            try :
                r = requests.get(url)
                if r.status_code != 200 :
                    if r.status_code == 500 :
                        error("Code 500. Attempts: " + str(attempts) + " from: " + url)
                        attempts -= 1
                        sleep(10)
                        continue
                    error("Code " + str(r.status_code) + " for node: " + str(node) + ". Attempts: " + str(attempts))
                    return

                data = loads(r.text)

                if len(data["data"]["result"]) == 0 :
                    attempts = 0
                    continue

                for result in data["data"]["result"] :
                    instance = re_sub(r'\..*', '', result["metric"]["instance"])
                    total += 1

                    if instance not in nodes :
                        emptyskipped[instance] = True
                        continue

                    if "dropletid" in result["metric"] and sys.argv[3].count(":9101"):
                        result["value"].append(instance)
                        instance = result["metric"]["dropletid"]
                        if ages :
                            if instance not in ages :
                                youngskipped[instance] = True
                                continue

                            result["value"].append(ages[instance])

                    if "cpu" in result["metric"] :
                        instance += "_" + result["metric"]["cpu"]

                    if "mode" in result["metric"] :
                        instance += "_" + result["metric"]["mode"]

                    print_result(instance, dumps(result["value"]))
                success = True
            
                break
            except Exception, e :
                attempts -= 1
                sleep(10)
                error("Exception. Attempts: " + str(attempts) + " from: " + str(e))
                continue

            if not success :
                print_result(node, "\"ERROR: RAN OUT OF ATTEMPTS TO: " + url + "\"")
                exit(1)

    error("Skipped (" + str(len(emptyskipped)) + " empty + " + str(len(youngskipped)) + " young) / " + str(total) + " total records.")

mysql_password = sys.argv[1]
mysql_hostname = "prod-mysql1d.nyc2.internal.digitalocean.com"
#mysql_hostname = "localhost"
mysql_username = "DO-read-only"
mysql_database = "digitalocean"

try :
    cnx = mysql.connector.connect(user = mysql_username, password = sys.argv[1], host = mysql_hostname, database = mysql_database)
except mysql.connector.Error as err :
    if err.errno == mysql.connector.errorcode.ER_ACCESS_DENIED_ERROR:
        error("Something is wrong with your user name or password")
    elif err.errno == mysql.connector.errorcode.ER_BAD_DB_ERROR:
        error("Database does not exist")
    else:
        error(err)
    exit(1)

error("Looking up regions...")
cursor = cnx.cursor()
cursor.execute("select regions.slug from regions")

slugs = []
for (slug) in cursor :
    # AMS1 was decommissioned, I believe, and does ams4 really exist?
    if slug[0] in ["ams1", "ams4"] :
        continue
    if slug[0] == "nyc1" :
        slugs.append("nbg1")
    else :
        slugs.append(slug[0])

cursor.close()

if len(sys.argv) >= 5 :
    node = sys.argv[4]
else :
    node = False

threads = 4

if not node :
    error("Looking up nodes with droplets...")
    cursor = cnx.cursor()
    cursor.execute("select servers.name as servers from servers left join droplets on servers.id = droplets.server_id where servers.is_node = 1 and droplets.is_active = 1 group by droplets.server_id limit 100000")

    nodes = {}
    error("Enumerating nodes...")
    while True :
        rows = cursor.fetchmany(threads)
        if not len(rows) :
            break
        for (node) in rows :
            nodes[node[0]] = True

    cursor.close()

    for slug in slugs :
        if slug == "nbg1" :
            slug = "nyc1"
        error("Enumerating droplets in slug " + str(slug) + "...")
        cursor = cnx.cursor()
        cursor.execute("select droplets.created_at,droplets.id,regions.slug from droplets left join regions on regions.id = droplets.region_id where regions.slug = '" + slug + "' and droplets.is_active = 1")
        skipped = 0
        dtotal = 0
        while True :
            rows = cursor.fetchmany(threads)
            if not len(rows) :
                break
            for (droplet) in rows :
                diff = int((datetime.now() - droplet[0]).total_seconds() / 60 / 60 / 24)
                if diff <= 0 :
                    skipped += 1
                    continue
                dtotal += 1
                ages[str(droplet[1])] = diff 
        cursor.close()
        error("Done, skipped: " + str(skipped) + " / " + str(dtotal))
else :
    nodes = {}
    ages = False
    nodes[node] = True

white = 0
if len(sys.argv) >= 6 :
    fn = sys.argv[5]
    if os.path.isfile(fn) :
        error("Opening whitelist: " + fn)
        fh = open(fn, "r")
        ages = loads(fh.read())
        white += len(ages)
        error("Added " + str(white) + " nodes to the whitelist.")
#pool.close()
#pool.join()

error("Polling")

age = sys.argv[2]

age_period = re_sub(r'[0-9]+', '', age)
age_amount = int(age.replace(age_period, ""))

error("You requested data for node: " + node if node else "all nodes")
error("Age: " + str(age_amount) + age_period)
error(str(len(slugs)) + " slugs: " + str(slugs))

# OK, now get a list of prometheus proxies from DNS
error("Enumerating DigitalOcean Region Prometheus Proxies...")

# Now, build a mapping between region and final prometheus API access point:

for lookup_slug in slugs :
    slug = lookup_slug
    if slug == "nyc1" :
        slug = "nbg1" 
    found = False
    scrapetype = "hypervisor"
    records = False
    if sys.argv[3].count(":9101") :
        # The consul records for droplet-scrape are stale. Not reliable right now.
        scrapetype = "droplet" 
        if slug in ["nyc2", "sgp1", "sfo2"] :
            records = ["http://prod-droplet-scrape01." + slug + ".internal.digitalocean.com:9090/"]
        if slug in ["nyc3"] and sys.argv[3].count("info_state") and sys.argv[3].count("changes") :
            records = ["http://prod-droplet-slowscrape01." + slug + ".internal.digitalocean.com:9090/"]

    if not records :
        records = get_srv(scrapetype + "-scrape-sharded.service." + slug + ".consul")

    if not records :
        records = get_srv(scrapetype + "-scrape.service." + slug + ".consul")
        if not records :
            error("Failed to SRV record for region: region: " + slug)
            continue
            #exit(1)

    access_points[lookup_slug] = records

    error("Final destination for " + lookup_slug + ": " + str(access_points[lookup_slug]))

end = time()
diff_secs = 0
if age_period == 's' :
    diff_factor = 1
elif age_period == 'm' :
    diff_factor = 60
elif age_period == 'h' :
    diff_factor = 3600
elif age_period == 'd' :
    diff_factor = 86400
elif age_period == 'w' :
    diff_factor = 604800
elif age_period == 'M' : # capital 'M' month instead of lower case
    diff_factor = 2678400
elif age_period == 'y' :
    diff_factor = 31536000

start = end - (diff_factor * age_amount)

msg("{")

if node :
    get_results(node)
else :
    for slug in slugs :
        get_results(slug)

msg("    \"end\" : false")
msg("}")
