# -*- coding: utf-8 -*-
# Description: prototypes for netdata python.d modules
# Author: Pawel Krupa (paulfantom)

import time
import sys
import os
import socket
import select
try:
    import urllib.request as urllib2
except ImportError:
    import urllib2

from subprocess import Popen, PIPE

import threading
import msg


class BaseService(threading.Thread):
    """
    Prototype of Service class.
    Implemented basic functionality to run jobs by `python.d.plugin`
    """
    def __init__(self, configuration=None, name=None):
        """
        This needs to be initialized in child classes
        :param configuration: dict
        :param name: str
        """
        threading.Thread.__init__(self)
        self._data_stream = ""
        self.daemon = True
        self.retries = 0
        self.retries_left = 0
        self.priority = 140000
        self.update_every = 1
        self.name = name
        self.override_name = None
        self.chart_name = ""
        self._dimensions = []
        self._charts = []
        self.__chart_set = False
        if configuration is None:
            self.error("BaseService: no configuration parameters supplied. Cannot create Service.")
            raise RuntimeError
        else:
            self._extract_base_config(configuration)
            self.timetable = {}
            self.create_timetable()

    def _extract_base_config(self, config):
        """
        Get basic parameters to run service
        Minimum config:
            config = {'update_every':1,
                      'priority':100000,
                      'retries':0}
        :param config: dict
        """
        try:
            self.override_name = config.pop('name')
        except KeyError:
            pass
        self.update_every = int(config.pop('update_every'))
        self.priority = int(config.pop('priority'))
        self.retries = int(config.pop('retries'))
        self.retries_left = self.retries
        self.configuration = config

    def create_timetable(self, freq=None):
        """
        Create service timetable.
        `freq` is optional
        Example:
            timetable = {'last': 1466370091.3767564,
                         'next': 1466370092,
                         'freq': 1}
        :param freq: int
        """
        if freq is None:
            freq = self.update_every
        now = time.time()
        self.timetable = {'last': now,
                          'next': now - (now % freq) + freq,
                          'freq': freq}

    def _run_once(self):
        """
        Executes self.update(interval) and draws run time chart.
        Return value presents exit status of update()
        :return: boolean
        """
        t_start = time.time()
        # check if it is time to execute job update() function
        if self.timetable['next'] > t_start:
            #msg.debug(self.chart_name + " will be run in " +
            #          str(int((self.timetable['next'] - t_start) * 1000)) + " ms")
            msg.debug(self.chart_name, "will be run in", str(int((self.timetable['next'] - t_start) * 1000)), "ms")
            return True

        since_last = int((t_start - self.timetable['last']) * 1000000)
        #msg.debug(self.chart_name +
        #          " ready to run, after " + str(int((t_start - self.timetable['last']) * 1000)) +
        #          " ms (update_every: " + str(self.timetable['freq'] * 1000) +
        #          " ms, latency: " + str(int((t_start - self.timetable['next']) * 1000)) + " ms)")
        msg.debug(self.chart_name,
                  "ready to run, after", str(int((t_start - self.timetable['last']) * 1000)),
                  "ms (update_every:", str(self.timetable['freq'] * 1000),
                  "ms, latency:", str(int((t_start - self.timetable['next']) * 1000)), "ms")
        if not self.update(since_last):
            self.error("update function failed.")
            return False
        t_end = time.time()
        self.timetable['next'] = t_end - (t_end % self.timetable['freq']) + self.timetable['freq']
        # draw performance graph
        run_time = str(int((t_end - t_start) * 1000))
        #run_time_chart = "BEGIN netdata.plugin_pythond_" + self.chart_name + " " + str(since_last) + '\n'
        #run_time_chart += "SET run_time = " + run_time + '\n'
        #run_time_chart += "END\n"
        #sys.stdout.write(run_time_chart)
        sys.stdout.write("BEGIN netdata.plugin_pythond_%s %s\nSET run_time = %s\nEND\n" % \
                         (self.chart_name, str(since_last), run_time))

        #msg.debug(self.chart_name + " updated in " + str(run_time) + " ms")
        msg.debug(self.chart_name, "updated in", str(run_time), "ms")
        self.timetable['last'] = t_start
        return True

    def run(self):
        """
        Runs job in thread. Handles retries.
        Exits when job failed or timed out.
        :return: None
        """
        self.timetable['last'] = time.time()
        while True:
            try:
                status = self._run_once()
            except Exception as e:
                msg.error("Something wrong: ", str(e))
                return
            if status:
                time.sleep(self.timetable['next'] - time.time())
                self.retries_left = self.retries
            else:
                self.retries_left -= 1
                if self.retries_left <= 0:
                    msg.error("no more retries. Exiting")
                    return
                else:
                    time.sleep(self.timetable['freq'])

    @staticmethod
    def _format(*args):
        params = []
        append = params.append
        for p in args:
            if p is None:
                append(p)
                continue
            if type(p) is not str:
                p = str(p)
            if ' ' in p:
                p = "'" + p + "'"
            append(p)
        return params

    def _line(self, instruction, *params):
        """
        Converts *params to string and joins them with one space between every one.
        :param params: str/int/float
        """
        #self._data_stream += instruction
        tmp = list(map((lambda x: "''" if x is None or len(x) == 0 else x), params))

        self._data_stream += "%s %s\n" % (instruction, str(" ".join(tmp)))

        # self.error(str(" ".join(tmp)))
        # for p in params:
        #     if p is None:
        #         p = ""
        #     else:
        #         p = str(p)
        #     if len(p) == 0:
        #         p = "''"
        #     if ' ' in p:
        #         p = "'" + p + "'"
        #     self._data_stream += " " + p
        #self._data_stream += "\n"

    def chart(self, type_id, name="", title="", units="", family="",
              category="", charttype="line", priority="", update_every=""):
        """
        Defines a new chart.
        :param type_id: str
        :param name: str
        :param title: str
        :param units: str
        :param family: str
        :param category: str
        :param charttype: str
        :param priority: int/str
        :param update_every: int/str
        """
        self._charts.append(type_id)
        #self._line("CHART", type_id, name, title, units, family, category, charttype, priority, update_every)

        p = self._format(type_id, name, title, units, family, category, charttype, priority, update_every)
        self._line("CHART", *p)

    def dimension(self, id, name=None, algorithm="absolute", multiplier=1, divisor=1, hidden=False):
        """
        Defines a new dimension for the chart
        :param id: str
        :param name: str
        :param algorithm: str
        :param multiplier: int/str
        :param divisor: int/str
        :param hidden: boolean
        :return:
        """
        try:
            int(multiplier)
        except TypeError:
            self.error("malformed dimension: multiplier is not a number:", multiplier)
            multiplier = 1
        try:
            int(divisor)
        except TypeError:
            self.error("malformed dimension: divisor is not a number:", divisor)
            divisor = 1
        if name is None:
            name = id
        if algorithm not in ("absolute", "incremental", "percentage-of-absolute-row", "percentage-of-incremental-row"):
            algorithm = "absolute"

        self._dimensions.append(str(id))
        if hidden:
            p = self._format(id, name, algorithm, multiplier, divisor, "hidden")
            #self._line("DIMENSION", id, name, algorithm, str(multiplier), str(divisor), "hidden")
        else:
            p = self._format(id, name, algorithm, multiplier, divisor)
            #self._line("DIMENSION", id, name, algorithm, str(multiplier), str(divisor))

        self._line("DIMENSION", *p)

    def begin(self, type_id, microseconds=0):
        """
        Begin data set
        :param type_id: str
        :param microseconds: int
        :return: boolean
        """
        if type_id not in self._charts:
            self.error("wrong chart type_id:", type_id)
            return False
        try:
            int(microseconds)
        except TypeError:
            self.error("malformed begin statement: microseconds are not a number:", microseconds)
            microseconds = ""

        self._line("BEGIN", type_id, str(microseconds))
        return True

    def set(self, id, value):
        """
        Set value to dimension
        :param id: str
        :param value: int/float
        :return: boolean
        """
        if id not in self._dimensions:
            self.error("wrong dimension id:", id, "Available dimensions are:", *self._dimensions)
            return False
        try:
            value = str(int(value))
        except TypeError:
            self.error("cannot set non-numeric value:", value)
            return False
        self._line("SET", id, "=", str(value))
        self.__chart_set = True
        return True

    def end(self):
        if self.__chart_set:
            self._line("END")
            self.__chart_set = False
        else:
            pos = self._data_stream.rfind("BEGIN")
            self._data_stream = self._data_stream[:pos]

    def commit(self):
        """
        Upload new data to netdata
        """
        print(self._data_stream)
        self._data_stream = ""

    def error(self, *params):
        """
        Show error message on stderr
        """
        msg.error(self.chart_name, *params)

    def debug(self, *params):
        """
        Show debug message on stderr
        """
        msg.debug(self.chart_name, *params)

    def info(self, *params):
        """
        Show information message on stderr
        """
        msg.info(self.chart_name, *params)

    def check(self):
        """
        check() prototype
        :return: boolean
        """
        msg.error("Service " + str(self.__module__) + "doesn't implement check() function")
        return False

    def create(self):
        """
        create() prototype
        :return: boolean
        """
        msg.error("Service " + str(self.__module__) + "doesn't implement create() function?")
        return False

    def update(self, interval):
        """
        update() prototype
        :param interval: int
        :return: boolean
        """
        msg.error("Service " + str(self.__module__) + "doesn't implement update() function")
        return False


class SimpleService(BaseService):
    def __init__(self, configuration=None, name=None):
        self.order = []
        self.definitions = {}
        BaseService.__init__(self, configuration=configuration, name=name)

    def _get_data(self):
        """
        Get some data
        :return: dict
        """
        return {}

    def check(self):
        """
        :return:
        """
        return True

    def create(self):
        """
        Create charts
        :return: boolean
        """
        data = self._get_data()
        if data is None:
            return False

        idx = 0
        for name in self.order:
            options = self.definitions[name]['options'] + [self.priority + idx, self.update_every]
            self.chart(self.chart_name + "." + name, *options)
            # check if server has this datapoint
            for line in self.definitions[name]['lines']:
                if line[0] in data:
                    self.dimension(*line)
            idx += 1

        self.commit()
        return True

    def update(self, interval):
        """
        Update charts
        :param interval: int
        :return: boolean
        """
        data = self._get_data()
        if data is None:
            self.debug("_get_data() returned no data")
            return False

        updated = False
        for chart in self.order:
            if self.begin(self.chart_name + "." + chart, interval):
                updated = True
                for dim in self.definitions[chart]['lines']:
                    try:
                        self.set(dim[0], data[dim[0]])
                    except KeyError:
                        pass
                self.end()

        self.commit()
        if not updated:
            self.error("no charts to update")

        return updated


class UrlService(SimpleService):
    def __init__(self, configuration=None, name=None):
        self.url = ""
        self.user = None
        self.password = None
        SimpleService.__init__(self, configuration=configuration, name=name)

    def __add_auth(self):
        passman = urllib2.HTTPPasswordMgrWithDefaultRealm()
        passman.add_password(None, self.url, self.user, self.password)
        authhandler = urllib2.HTTPBasicAuthHandler(passman)
        opener = urllib2.build_opener(authhandler)
        urllib2.install_opener(opener)

    def _get_raw_data(self):
        """
        Get raw data from http request
        :return: str
        """
        raw = None
        try:
            f = urllib2.urlopen(self.url, timeout=self.update_every)
        except Exception as e:
            self.error(str(e))
            return None

        try:
            raw = f.read().decode('utf-8')
        except Exception as e:
            self.error(str(e))
        finally:
            f.close()
        return raw

    def check(self):
        """
        Format configuration data and try to connect to server
        :return: boolean
        """
        if self.name is None or self.name == str(None):
            self.name = 'local'
            self.chart_name += "_" + self.name
        else:
            self.name = str(self.name)
        try:
            self.url = str(self.configuration['url'])
        except (KeyError, TypeError):
            pass
        try:
            self.user = str(self.configuration['user'])
        except (KeyError, TypeError):
            pass
        try:
            self.password = str(self.configuration['pass'])
        except (KeyError, TypeError):
            pass

        if self.user is not None and self.password is not None:
            self.__add_auth()

        if self._get_data() is not None:
            return True
        else:
            return False


class SocketService(SimpleService):
    def __init__(self, configuration=None, name=None):
        self._sock = None
        self._keep_alive = False
        self.host = "localhost"
        self.port = None
        self.unix_socket = None
        self.request = ""
        self.__socket_config = None
        SimpleService.__init__(self, configuration=configuration, name=name)

    def _connect(self):
        """
        Recreate socket and connect to it since sockets cannot be reused after closing
        Available configurations are IPv6, IPv4 or UNIX socket
        :return:
        """
        try:
            if self.unix_socket is None:
                if self.__socket_config is None:
                    # establish ipv6 or ipv4 connection.
                    for res in socket.getaddrinfo(self.host, self.port, socket.AF_UNSPEC, socket.SOCK_STREAM):
                        try:
                            af, socktype, proto, canonname, sa = res
                            self._sock = socket.socket(af, socktype, proto)
                        except socket.error as msg:
                            self._sock = None
                            continue
                        try:
                            self._sock.connect(sa)
                        except socket.error as msg:
                            self._disconnect()
                            continue
                        self.__socket_config = res
                        break
                else:
                    # connect to socket with previously established configuration
                    try:
                        af, socktype, proto, canonname, sa = self.__socket_config
                        self._sock = socket.socket(af, socktype, proto)
                        self._sock.connect(sa)
                    except socket.error as msg:
                        self._disconnect()
            else:
                # connect to unix socket
                self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                self._sock.connect(self.unix_socket)
        except Exception as e:
            self.error(str(e),
                       "Cannot create socket with following configuration: host:", str(self.host),
                       "port:", str(self.port),
                       "socket:", str(self.unix_socket))
            self._sock = None
        self._sock.setblocking(0)

    def _disconnect(self):
        """
        Close socket connection
        :return:
        """
        try:
            self._sock.shutdown(2)  # 0 - read, 1 - write, 2 - all
            self._sock.close()
        except Exception:
            pass
        self._sock = None

    def _send(self):
        """
        Send request.
        :return: boolean
        """
        # Send request if it is needed
        if self.request != "".encode():
            try:
                self._sock.send(self.request)
            except Exception as e:
                self._disconnect()
                self.error(str(e),
                           "used configuration: host:", str(self.host),
                           "port:", str(self.port),
                           "socket:", str(self.unix_socket))
                return False
        return True

    def _receive(self):
        """
        Receive data from socket
        :return: str
        """
        data = ""
        while True:
            try:
                ready_to_read, _, in_error = select.select([self._sock], [], [], 15)
            except Exception as e:
                self.debug("SELECT", str(e))
                self._disconnect()
                break
            if len(ready_to_read) > 0:
                buf = self._sock.recv(4096)
                if len(buf) == 0 or buf is None:  # handle server disconnect
                    break
                data += buf.decode()
                if self._check_raw_data(data):
                    break
            else:
                self.error("Socket timed out.")
                self._disconnect()
                break

        return data

    def _get_raw_data(self):
        """
        Get raw data with low-level "socket" module.
        :return: str
        """
        if self._sock is None:
            self._connect()

        # Send request if it is needed
        if not self._send():
            return None

        data = self._receive()

        if not self._keep_alive:
            self._disconnect()

        return data

    def _check_raw_data(self, data):
        """
        Check if all data has been gathered from socket
        :param data: str
        :return: boolean
        """
        return True

    def _parse_config(self):
        """
        Parse configuration data
        :return: boolean
        """
        if self.name is None or self.name == str(None):
            self.name = ""
        else:
            self.name = str(self.name)
        try:
            self.unix_socket = str(self.configuration['socket'])
        except (KeyError, TypeError):
            self.debug("No unix socket specified. Trying TCP/IP socket.")
            try:
                self.host = str(self.configuration['host'])
            except (KeyError, TypeError):
                self.debug("No host specified. Using: '" + self.host + "'")
            try:
                self.port = int(self.configuration['port'])
            except (KeyError, TypeError):
                self.debug("No port specified. Using: '" + str(self.port) + "'")
        try:
            self.request = str(self.configuration['request'])
        except (KeyError, TypeError):
            self.debug("No request specified. Using: '" + str(self.request) + "'")
        self.request = self.request.encode()


class LogService(SimpleService):
    def __init__(self, configuration=None, name=None):
        self.log_path = ""
        self._last_position = 0
        # self._log_reader = None
        SimpleService.__init__(self, configuration=configuration, name=name)
        self.retries = 100000  # basically always retry

    def _get_raw_data(self):
        """
        Get log lines since last poll
        :return: list
        """
        lines = []
        try:
            if os.path.getsize(self.log_path) < self._last_position:
                self._last_position = 0
            elif os.path.getsize(self.log_path) == self._last_position:
                self.debug("Log file hasn't changed. No new data.")
                return None
            with open(self.log_path, "r") as fp:
                fp.seek(self._last_position)
                for i, line in enumerate(fp):
                    lines.append(line)
                self._last_position = fp.tell()
        except Exception as e:
            self.error(str(e))

        if len(lines) != 0:
            return lines
        else:
            self.error("No data collected.")
            return None

    def check(self):
        """
        Parse basic configuration and check if log file exists
        :return: boolean
        """
        if self.name is not None or self.name != str(None):
            self.name = ""
        else:
            self.name = str(self.name)
        try:
            self.log_path = str(self.configuration['path'])
        except (KeyError, TypeError):
            self.error("No path to log specified. Using: '" + self.log_path + "'")

        if os.access(self.log_path, os.R_OK):
            return True
        else:
            self.error("Cannot access file: '" + self.log_path + "'")
            return False

    def create(self):
        status = SimpleService.create(self)
        self._last_position = 0
        return status


class ExecutableService(SimpleService):
    #command_whitelist = ['exim', 'postqueue']
    bad_substrings = ('&', '|', ';', '>', '<')

    def __init__(self, configuration=None, name=None):
        self.command = ""
        SimpleService.__init__(self, configuration=configuration, name=name)

    def _get_raw_data(self):
        """
        Get raw data from executed command
        :return: str
        """
        try:
            p = Popen(self.command, stdout=PIPE, stderr=PIPE)
        except Exception as e:
            self.error("Executing command", self.command, "resulted in error:", str(e))
            return None
        data = []
        for line in p.stdout.readlines():
            data.append(str(line.decode()))

        if len(data) == 0:
            self.error("No data collected.")
            return None

        return data

    def check(self):
        """
        Parse basic configuration, check if command is whitelisted and is returning values
        :return: boolean
        """
        if self.name is not None or self.name != str(None):
            self.name = ""
        else:
            self.name = str(self.name)
        try:
            self.command = str(self.configuration['command'])
        except (KeyError, TypeError):
            self.error("No command specified. Using: '" + self.command + "'")
        self.command = self.command.split(' ')
        #if self.command[0] not in self.command_whitelist:
        #    self.error("Command is not whitelisted.")
        #    return False

        for arg in self.command[1:]:
            if any(st in arg for st in self.bad_substrings):
                self.error("Bad command argument:" + " ".join(self.command[1:]))
                return False
        # test command and search for it in /usr/sbin or /sbin when failed
        base = self.command[0].split('/')[-1]
        if self._get_raw_data() is None:
            for prefix in ['/sbin/', '/usr/sbin/']:
                self.command[0] = prefix + base
                if os.path.isfile(self.command[0]):
                    break
                #if self._get_raw_data() is not None:
                #    break

        if self._get_data() is None or len(self._get_data()) == 0:
            self.error("Command", self.command, "returned no data")
            return False
        return True
