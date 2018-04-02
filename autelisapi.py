# Autelis Pool Control wrapper class
# Designed to work with Jandy TCP Serial Port Firmwares v. 1.6.9
# and higher and Pentair TCP Serial Port Firmwares v. 1.6.7 and higher

import re
import socket
import xml.etree.ElementTree as xml
import logging
import sys

import requests

# Parameters for Pool Control HTTP Command Interface
_STATUS_ENDPOINT = "status.xml"
_COMMAND_ENDPOINT = "set.cgi"
_AUTELIS_ON_VALUE = 1
_AUTELIS_OFF_VALUE = 0

# Parameters for Pool Control TCP Serial Port interface
_CONTROLLER_TCP_PORT = 6000
_TEST_TCP_MSG = b"#OPMODE?\r"
_TEST_RTN_SUCCESS = b"!00 OPMODE="
_STATUS_UPDATE_MATCH_PATTERN = r"!00 ([A-Z0-9]+)=([A-Z0-9]+) ?[FC]?\r\n"
_BUFFER_SIZE = 32

class AutelisInterface(object):

    # Primary constructor method
    def __init__(self, controllerAddr, userName, password, logger=None):

        # declare instance variables
        self.controllerAddr = controllerAddr
        self._userName = userName
        self._password = password

        # setup basic console logger for debugging
        if logger is None:
            logging.basicConfig(format='%(asctime)s %(levelname)s:%(message)s', level=logging.DEBUG)
            self._logger = logging.getLogger() # Root logger
        else:
            self._logger = logger

    # Gets the status XML from the Pool Controller
    def get_status(self):

        self._logger.debug("In get_status()...")

        try:
            response = requests.get(
                "http://{host_addr}/{device_list_endpoint}".format(
                    host_addr=self.controllerAddr,
                    device_list_endpoint=_STATUS_ENDPOINT
                ),
                auth=(self._userName, self._password),
                timeout=3.05
            )
            response.raise_for_status()    # Raise HTTP errors to be handled in exception handling

        # Allow timeout and connection errors to be ignored - log and return no XML
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as e:
            self._logger.warning("HTTP GET in get_status() failed - %s", str(e))
            return None
        except:
            self._logger.error("Unexpected error occured - %s", sys.exc_info()[0])
            raise

        statusXML = xml.fromstring(response.text)
        if statusXML.tag == "response":
            return statusXML
        else:
            self._logger.warning("%s returned invalid XML in response", response.url)
            return None

    # Set the named attribute of the named element to the specified value
    def send_command(self, element, label, value):

        self._logger.debug("In send_command(): Element %s, Label %s, Value %s", element, label, value)

        try:
            response = requests.get(
                "http://{host_addr}/{device_set_endpoint}?name={name}&{label}={value}".format(
                    host_addr=self.controllerAddr,
                    device_set_endpoint=_COMMAND_ENDPOINT,
                    name=element,
                    label=label,
                    value=str(int(value))
                ),
                auth=(self._userName, self._password),
                timeout=3.05
            )
            response.raise_for_status()    # Raise HTTP errors to be handled in exception handling

        # Allow timeout and connection errors to be ignored - log and return false
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as e:
            self._logger.warning("HTTP GET in send_command() failed - %s", str(e))
            return False
        except:
            self._logger.error("Unexpected error occured - %s", sys.exc_info()[0])
            raise
        else:
            self._logger.debug("GET returned successfully - %s", response.text)
            return True

    def on(self, element):
        return self.send_command(element, "value", _AUTELIS_ON_VALUE)

    def off(self, element):
        return self.send_command(element, "value", _AUTELIS_OFF_VALUE)

    def set_temp(self, element, value):
        return self.send_command(element, "temp", value)

    def set_heat_setting(self, element, value):    # for Pentair compatibility
        return self.send_command(element, "hval", value)

# Monitors the TCP connection for status updates from the Pool Controller and forwards
# to Node Server in real time - must be executed on seperate, non-blocking thread
def status_listener(controllerAddr, statusUpdateCallback=None, logger=None):

    # setup basic console logger for debugging
    if logger == None:
        logging.basicConfig(format='%(asctime)s %(levelname)s:%(message)s', level=logging.DEBUG)
        logger = logging.getLogger() # Root logger

    logger.debug("In status_listener...")

    # Open a socket for communication with the Pool Controller
    conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        conn.connect((controllerAddr, _CONTROLLER_TCP_PORT))
    except (socket.error, socket.herror, socket.gaierror) as e:
        logger.error("Unable to establish TCP connection with Pool Controller. Socket error: %s", str(e))
        conn.close()
        return False
    except:
        conn.close()
        raise

    # Loop continuously and Listen for status messages over TCP connection
    while True:

        # Get next status message
        try:
            conn.settimeout(600) # If no messages in 10 minutes, then check connection
            msg = conn.recv(_BUFFER_SIZE)

        except socket.timeout:

            # Check connection
            try:
                conn.settimeout(2)
                conn.send(_TEST_TCP_MSG)
                msg = conn.recv(_BUFFER_SIZE)
            except socket.timeout:
                logger.error("Pool Controller did not respond to test message - connection closed.")
                conn.close()
                return False
            except socket.error as e:
                logger.error("TCP Connection to Pool Controller unexpectedly closed. Socket error: %s", str(e))
                conn.close()
                return False
            except:
                conn.close()
                raise

            # check returned data for success
            if not _TEST_RTN_SUCCESS in msg:
                logger.error("Pool Controller returned invalid data ('%s') - connection closed.", msg.decode("utf-8"))
                conn.close()
                return False

        except socket.error as e:
            logger.error("TCP Connection to Pool Controller unexpectedly closed. Socket error: %s", str(e))
            conn.close()
            return False
        except:
            conn.close()
            raise

        # If msg is not empty, process status request
        if len(msg) > 0:

            # See if the status update message matches our regex pattern
            matches = re.match(_STATUS_UPDATE_MATCH_PATTERN, msg.decode("utf-8"))
            if matches:

                # pull the pertinent data out of the message
                cmd = matches.groups()[0]
                val = matches.groups()[1]

                logger.debug("Status update message received from Pool Controller: Command %s, Value %s", cmd, val)

                # call status update callback function
                if not statusUpdateCallback is None:
                    if not statusUpdateCallback(cmd_to_element(cmd), val_to_text(val)):
                        logger.warning("Unhandled status update from Pool Controller - %s", cmd)

            else:
                logger.warning("Invalid status message received from Pool Controller - %s", msg.decode("utf-8"))

# Convert the TCP Serial Port Interface command words to
# element tags matching the HTTP Command Interface
def cmd_to_element(cmd):

    if cmd[:3] == "CIR":    # for Pentair compatibility
        circuitNum = int(cmd[3:])
        if circuitNum >= 41 and circuitNum <= 50:
            return "feature" + str(circuitNum - 40)
        else:
            return "circuit" + cmd[3:]
    elif cmd == "AIRTMP":
        return "airtemp"
    elif cmd == "SPATMP":
        return "spatemp"
    elif cmd == "SOLHT":
        return "solarht"
    elif cmd == "SOLTMP":
        return "solartemp"
    elif cmd == "WFALL":
        return "waterfall"
    elif cmd == "CLEAN":
        return "cleaner"
    elif cmd == "OPTIONS":
        return "dip"
    elif cmd == "UNITS":
        return "tempunits"
    elif cmd == "POOLTMP":
        return "pooltemp"
    elif cmd == "POOLTMP2":
        return "pooltemp"
    else:
        return cmd.lower()

# Convert the TCP Serial Port Interface value to
# element text matching the HTTP Command Interface
def val_to_text(val):

    if val == "AUTO":
        return "0"
    elif val == "SERVICE":
        return "1"
    elif val == "TIMEOUT":
        return "2"
    elif val == "TRUE":
        return "1"
    elif val == "FALSE":
        return "0"
    elif val == "T":
        return "1"
    elif val == "F":
        return "0"
    elif val == "ON":
        return "1"
    elif val == "OFF":
        return "0"
    elif val == "HEATER":    # for Pentair compatibility
        return "1"
    elif val == "SOLPREF":    # for Pentair compatibility
        return "2"
    elif val == "SOLAR":    # for Pentair compatibility
        return "3"
    else:
        return val
