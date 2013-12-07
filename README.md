tornado-relay
=============

graphite carbon-relay built on tornado framework

This is carbon-relay written using tornadoweb.

Requirements
------------
 * tornado framework installed
 * whisper and carbon installed and placed in PYTHONPATH

Current support for:

 * line and pickle interfaces
 * rules-based and consistent hashing routers
 * replication factor

Planned:

 * AMQP

Start
----
<code>
./tornado-relay.py --help
Usage: ./tornado-relay.py [OPTIONS]

Options:

  --config                         path to config file
  --destinations                   carbon DESTINATIONS list, comma separated,
                                   in format hostname:port:instance
  --help                           show this help information
  --instance                       instance name (default a)
  --line_port                      port for line interface (default 2013)
  --log_file_max_size              max size of log files before rollover
                                   (default 100000000)
  --log_file_num_backups           number of log files to keep (default 10)
  --log_file_prefix=PATH           Path prefix for log files. Note that if you
                                   are running multiple tornado processes,
                                   log_file_prefix must be different for each
                                   of them (e.g. include the port number)
  --log_to_stderr                  Send log output to stderr (colorized if
                                   possible). By default use stderr if
                                   --log_file_prefix is not set and no other
                                   logging is configured.
  --logging=debug|info|warning|error|none 
                                   Set the Python log level. If 'none', tornado
                                   won't touch the logging configuration.
                                   (default info)
  --maxqlen                        outpuit queue length (default 100000)
  --pickle_port                    port for pickle interface (default 2014)
  --processes                      num of parallel processes (default 2)
  --replication                    replication factor (default 1)
  --rules                          carbon rules file
</code>
