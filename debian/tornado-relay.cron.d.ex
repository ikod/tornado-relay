#
# Regular cron jobs for the tornado-relay package
#
0 4	* * *	root	[ -x /usr/bin/tornado-relay_maintenance ] && /usr/bin/tornado-relay_maintenance
