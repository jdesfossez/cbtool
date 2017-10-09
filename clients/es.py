#!/usr/bin/env python
import json
import iso8601
from datetime import datetime
import logging
from time import time as timest
from sys import argv
from elasticsearch import Elasticsearch, RequestsHttpConnection

#es_logger = logging.getLogger('elasticsearch')
#es_logger.setLevel(logging.DEBUG)
#consoleHandler = logging.StreamHandler()
#es_logger.addHandler(consoleHandler)
#import httplib
#httplib.HTTPConnection.debuglevel = 3
#httplib.HTTPSConnection.debuglevel = 3

es = Elasticsearch([dict(host = argv[2], port = int(argv[3]), http_auth = argv[4] if argv[4] != "" else None, use_ssl=True if argv[1] == 'https' else False, verify_certs = True if argv[1] == 'https' else False)], connection_class = RequestsHttpConnection, timeout = 120)

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

trange = argv[5]
event_id = argv[6]

def by_time( a ):
  return iso8601.parse_date(a["_source"]["@timestamp"])

data = {"memory_iterations" : [], "block_iterations" : []}

result = es.search(index="backend-*", body = make_query(trange, event_id))

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

    miter = len(data["memory_iterations"])
    print("Memory iterations: " + str(miter))
    mbps = data["memory_iterations"][-1]["ram"]["mbps"] if miter else -1
    print("Memory Mb/s: " + str(mbps))
    block_length = data["block_iterations"][-1]["len"]
    block_start = iso8601.parse_date(data["block_iterations"][0]["timestamp"])
    block_stop = iso8601.parse_date(data["block_iterations"][-1]["timestamp"])
    block_seconds = (block_stop - block_start).total_seconds()
    print("Block iterations: " + str(len(data["block_iterations"])))
    print("Block Mb/s: " + str(block_length / block_seconds / 1024 / 1024))
    print("final: " + str(data["final"]))
else :
    print " no data. =("
