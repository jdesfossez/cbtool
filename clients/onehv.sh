#!/usr/bin/env bash

if [ x"$1" == x ] ; then
	echo "Need mysql slave password"
	exit 1
fi

pass=$1
shift

if [ x"$1" == x ] ; then
	echo "Need source HV"
	exit 1
fi

hv=$1
shift

if [ x"$1" == x ] ; then
	echo "Need droplet limit"
	exit 1
fi

limit=$1
shift

if [ x"$1" == x ] ; then
	echo "Need target HV"
	exit 1
fi

targetid=$1
shift

#if [ x"$1" == x ] ; then
#	echo "Need droplet size upper limit"
#	exit 1
#fi
#
#limit=$1
#shift

current=1

echo "Polling $hv for droplets..."
# The last parameter is a whitelist generated from a previous deliver.py command. A search within a search, so to speak, or a cached search."
#~/do/cbtool/clients/deliver.py $pass 1d "droplets_info_state{instance='NODE.SLUG.internal.digitalocean.com:9101'}==1 and droplets_info_cephsecretmatch{instance='NODE.SLUG.internal.digitalocean.com:9101'}>=0" $hv /tmp/$hv.json > /tmp/droplets.json 2>/dev/null
~/do/cbtool/clients/deliver.py $pass 2h "droplets_info_state{instance='NODE.SLUG.internal.digitalocean.com:9101'}==1 and droplets_info_cephsecretmatch{instance='NODE.SLUG.internal.digitalocean.com:9101'}>=0" $hv > /tmp/droplets.json 2>/dev/null

if [ $? -gt 0 ] ; then
    echo "Failed to poll for droplets"
	exit 1
fi

droplets=($(jq 'keys[]' /tmp/droplets.json))

for droplet in ${droplets[@]} ; do
	droplet=$(echo $droplet | sed "s/\"//g")
	if [ x"$droplet" == xend ] ; then 
		continue
	fi

	echo "Initiating migration for: Droplet-$droplet"

	rm -f /tmp/$droplet.log

	python -u ~/do/cbtool/clients/onedroplet.py --slavepass $pass --email mhines@digitalocean.com --targethv ${targetid} --droplet ${droplet} #--nokafka --eshost kibana-deprecated.internal.digitalocean.com --esport 9200

	if [ $? -gt 0 ] ; then
		echo "Failure during migration. Aborting."
		break
	fi
	
	if (( $current >= $limit )) ; then
		echo "We've reached the maximum number of droplets: $limit"
		break
	else
		echo "Migrated $current / ${limit}..."
		((current=current+1))
	fi
done

echo "Evacuation of $hv complete."
