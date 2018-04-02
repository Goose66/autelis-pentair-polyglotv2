#!/usr/bin/python3
# Polglot Node Server for Pentair controller through Autelis Pool Control Interface

import sys
import time
import xml.etree.ElementTree as XML
import autelisapi
import polyinterface

_ISY_BOOL_UOM = 2 # Used for reporting status values for Controller node
_ISY_INDEX_UOM = 25 # Index UOM for custom states (must match editor/NLS in profile):
_ISY_TEMP_F_UOM = 17 # UOM for temperatures (farenheit)
_ISY_TEMP_C_UOM = 4 # UOM for temperatures (celcius)
_ISY_THERMO_MODE_UOM = 67 # UOM for thermostat mode
_ISY_THERMO_HCS_UOM = 66 # UOM for thermostat heat/cool state
_ISY_VOLT_UOM = 72 # UOM for Voltage

_LOGGER = polyinterface.LOGGER

# Node class for equipment (circuits and features)
class Equipment(polyinterface.Node):

    id = "EQUIPMENT"

    # Turn equipment ON
    def cmd_don(self, command):
        if self.controller.autelis.on(self.address):
            self.setDriver("ST", 1)
        else:
            _LOGGER.warning("Call to Pool Controller in DON command handler failed for node %s.", self.address)

    # Turn equipment OFF
    def cmd_dof(self, command):
        if self.controller.autelis.off(self.address):
            self.setDriver("ST", 0)
        else:
            _LOGGER.warning("Call to Pool Controller in DOF command handler failed for node %s.", self.address)

    # Run update function in parent before reporting driver values
    def query(self):
        self.controller.update_node_states(False)
        self.reportDrivers()

    drivers = [{"driver": "ST", "value": 0, "uom": _ISY_INDEX_UOM}]
    commands = {
        "DON": cmd_don,
        "DOF": cmd_dof
    }

# Node class for temperature controls (pool heat, spa heat, etc.)
class TempControl(polyinterface.Node):

    id = "TEMP_CONTROL"

    # Override init to handle temp units
    def __init__(self, controller, primary, address, name, tempUnit):
        self.set_temp_unit(tempUnit)
        super(TempControl, self).__init__(controller, primary, address, name)
        

    # Setup node_def_id and drivers for tempUnit
    def set_temp_unit(self, tempUnit):
        
        # set the id of the node for the ISY to use from the nodedef
        if tempUnit == "C":
            self.id = "TEMP_CONTROL_C"
        else:
            self.id = "TEMP_CONTROL"
            
        # update the drivers in the node
        for driver in self.drivers:
            if driver["driver"] in ("ST", "CLISPH", "CLISPC"):
                driver["uom"] = _ISY_TEMP_C_UOM if tempUnit == "C" else _ISY_TEMP_F_UOM

    # Set set point temperature
    def cmd_set_temp(self, command):
        
        value = int(command.get("value"))

        # determine setpoint element to change based on the node address
        if self.address == "poolht":
            name = "poolsp"
        elif self.address == "poolht2":
            name = "poolsp2"
        elif self.address == "spaht":
            name = "spasp"
        else:
            _LOGGER.warning("No setpoint for node %s - SET_TEMP command ignored.", self.address)
            return

        # set the setpoint element
        if self.controller.autelis.set_temp(name, value):
            self.setDriver("CLISPH", value)
        else:
            _LOGGER.warning("Call to Pool Controller in SET_TEMP command handler failed for node %s.", self.address)

    # Set the heat setting from the thermostat mode value
    def cmd_set_mode(self, command):
        
        value = int(command.get("value"))

        # Translate the ISY thermostat mode to the Pentair heater setting
        if value == 1:      # Heat
            setting = 1     # heater
        elif value == 3:    # Auto
            setting = 2     # solar preferred
        elif value == 4:    # Aux/Emergency Heat
            setting = 3     # solar only
        else:
            setting = 0     # Off        

        # set the heater setting
        if self.parent.autelis.set_heat_setting(self.address, setting):
            self.setDriver("CLIMD", value)
        else:
            _LOGGER.warning("Call to Pool Controller in SET_MODE command handler failed for node %s.", self.address)

    # Update the mode and heat/cool state drivers from the status values from the Pentair controller
    def update_thermo_drivers(self, setting, htstatus, report=True):

        # Translate the heater setting to the ISY thermostat mode
        if setting == 1:                             # heater
            self.setDriver("CLIMD", 1, report)  # Heat
        elif setting == 2:                           # solar preferred
            self.setDriver("CLIMD", 3, report)  # Auto
        elif setting == 3:                           # solar only
            self.setDriver("CLIMD", 4, report)  # Aux/Emergency Heat
        else:
            self.setDriver("CLIMD", 0, report)  # Off

        # Translate the heating status bit values to the ISY Heat/Cool Status value
        if self.address == "spaht":
            
            if htstatus & int("0010", 2):           # spa heat
                self.setDriver("CLIHCS", 1, report) # Heating

            elif htstatus & int("1000", 2):         # spa solar
                self.setDriver("CLIHCS", 7, report) # Aux Heat

            else:
                self.setDriver("CLIHCS", 0, report) # Idle

        elif self.address == "poolht":

            if htstatus & int("0001", 2):           # pool heat
                self.setDriver("CLIHCS", 1, report) # Heating

            elif htstatus & int("0100", 2):         # pool solar
                self.setDriver("CLIHCS", 7, report) # Aux Heat

            else:
                self.setDriver("CLIHCS", 0, report) # Idle

    # Run update function in parent before reporting driver values
    def query(self):
        self.controller.update_node_states(False)
        self.reportDrivers()

    drivers = [
        {"driver": "ST", "value": 0, "uom": _ISY_TEMP_F_UOM},
        {"driver": "CLISPH", "value": 0, "uom": _ISY_TEMP_F_UOM},
        {"driver": "CLIMD", "value": 0, "uom": _ISY_THERMO_MODE_UOM},
        {"driver": "CLIHCS", "value": 0, "uom": _ISY_THERMO_HCS_UOM},
        {"driver": "CLISPC", "value": 0, "uom": _ISY_TEMP_F_UOM}
    ]
    commands = {
        "SET_MODE": cmd_set_mode,
        "SET_SPH": cmd_set_temp
    }

# Node class for controller
class Controller(polyinterface.Controller):

    id = "CONTROLLER"

    def __init__(self, poly):
        super(Controller, self).__init__(poly)
        self.name = "controller"
        self.autelis = None
        self.pollingInterval = 20
        self.ignoresolar = False
        self.lastPoll = 0
        self.currentTempUnit = "F"
        self.threadMonitor = None

    # Setup node_def_id and drivers for temp unit
    def set_temp_unit(self, tempUnit):
        
        # Update the drivers to the new temp unit
        for driver in self.drivers:
            if driver["driver"] in ("CLITEMP", "GV9"):
                driver["uom"] = _ISY_TEMP_C_UOM if tempUnit == "C" else _ISY_TEMP_F_UOM

        # update the node definition in the Polyglot DB
        self.updateNode(self)

        self.currentTempUnit = tempUnit
       
    # change the temp units utilized by the nodeserver
    def change_temp_units(self, newTempUnit):
         
        # update the temp unit for the temp control nodes
        for addr in self.nodes:
            node = self.nodes[addr]
            if node.id in ("TEMP_CONTROL", "TEMP_CONTROL_C"):
               node.set_temp_unit(newTempUnit) 
               self.updateNode(node) # Calls ISY REST change command to change node_def_id
        
        # update the temp unit for the controller node
        self.set_temp_unit(newTempUnit)
        
    # Start the nodeserver
    def start(self):

        _LOGGER.info("Started Autelis Nodeserver...")

        # get controller information from custom parameters
        try:
            customParams = self.poly.config["customParams"]
            ip = customParams["ipaddress"]
            username = customParams["username"]
            password = customParams["password"]
        except KeyError:
            _LOGGER.error("Missing controller settings in configuration.")
            raise

        # get polling intervals and configuration settings from custom parameters
        try:
            self.pollingInterval = int(customParams["pollinginterval"])
        except (KeyError, ValueError):
            self.pollingInterval = 60
        try:
            self.ignoresolar = bool(customParams["ignoresolar"])
        except (KeyError, ValueError):
            self.ignoresolar = False
        
        # dump the self._nodes to the log
        #_LOGGER.debug("Current Node Configuration: %s", str(self._nodes))

        # create a object for the autelis interface
        self.autelis = autelisapi.AutelisInterface(ip, username, password, _LOGGER)

        #  setup the nodes from the autelis pool controller
        self.discover_nodes() 
    
    # called every long_poll seconds
    def longPoll(self):

        pass

    # called every short_poll seconds
    def shortPoll(self):

        # if node server is not setup yet, return
        if self.autelis is None:
            return

        currentTime = time.time()

        # check for elapsed polling interval
        if (currentTime - self.lastPoll) >= self.pollingInterval:

            # update the node states
            _LOGGER.debug("Updating node states in AuteliseNodeServer.shortPoll()...")
            self.update_node_states(True) # Update node states
            self.lastPoll = currentTime

    # Override query to report driver values and child driver values
    def query(self):

        # update all nodes - don't report
        self.update_node_states(False)

        # report drivers of all nodes
        for addr in self.nodes:
            self.nodes[addr].reportDrivers()

    # Create nodes for all devices from the autelis interface
    def discover_nodes(self):

        # get the status XML from the autelis device
        statusXML = self.autelis.get_status()

        if statusXML is None:
            _LOGGER.error("No status XML returned from Autelis device on startup.")
            sys.exit("Failure on intial communications with Autelis device.")   

        else:

            # dump the XML to the log
            _LOGGER.debug("Status XML: %s", "\n" + XML.tostring(statusXML).decode())

            # Get the temp units and update the controller node if needed
            temp = statusXML.find("temp")
            tempUnit = temp.find("tempunits").text
            if tempUnit != self.currentTempUnit: # If not "F"              
                self.set_temp_unit(tempUnit)
 
            # create TEMP_CONTROL nodes for poolht and spaht
            for addr in ("poolht", "spaht"):
                tempNode = TempControl(self, self.address, addr, addr, tempUnit)
                self.addNode(tempNode)

            # Iterate equipment child elements and process each
            equipment = statusXML.find("equipment")
            for element in list(equipment):

                # Only process elements that have text values (assuming blank
                # elements are not part of the installed/configured equipment).
                 if not element.text is None:

                    addr = element.tag

                    # Create the EQUIPMENT node
                    equipNode = Equipment(self, self.address, addr, addr)
                    self.addNode(equipNode)
                        
    # Creates or updates the state values of all nodes from the autelis interface
    def update_node_states(self, report=True):

        # get the status XML from the autelis device
        statusXML = self.autelis.get_status()

        if statusXML is None:
            _LOGGER.warning("No XML returned from get_status().")
            self.setDriver("GV0", 0, report)

        else:

            # dump the XML to the log
            #_LOGGER.debug("Status XML: %s", "\n" + XML.tostring(statusXML).decode())
            
            # Parse status XML
            system = statusXML.find("system")
            equipment = statusXML.find("equipment")
            temp = statusXML.find("temp")

            # Check for change in temp units on device
            # Note: Should be picked up in TCP connection monitoring but just in case 
            tempUnit = temp.find("tempunits").text
            if tempUnit != self.currentTempUnit:
                self.change_temp_units(tempUnit)

            # Get the element values for the controller node
            runstate = int(system.find("runstate").text)
            opmode = int(system.find("opmode").text)
            freeze = int(system.find("freeze").text)
            waterSensor = int(system.find("sensor1").text)
            solarSensor = int(system.find("sensor2").text)
            airSensor = int(system.find("sensor3").text)
            airTemp = int(temp.find("airtemp").text)
            solarTemp = int(temp.find("soltemp").text)

            # Update the controller node drivers
            self.setDriver("GV0", runstate, report)
            self.setDriver("GV1", opmode, report)
            self.setDriver("GV2", freeze, report)
            self.setDriver("GV3", waterSensor, report)
            self.setDriver("GV4", solarSensor, report)
            self.setDriver("GV5", airSensor, report)
            self.setDriver("CLITEMP", airTemp, report)
            self.setDriver("GV9", solarTemp, report)

            # Process poolht temp control elements
            node = self.nodes["poolht"]
            htstatus = int(temp.find("htstatus").text)
            setting = int(temp.find("poolht").text)
            setPoint = int(temp.find("poolsp").text)
            currentTemp = int(temp.find("pooltemp").text)

            # Update node driver values
            node.setDriver("ST", currentTemp, report)
            node.setDriver("CLISPH", setPoint, report)
            node.update_thermo_drivers(setting, htstatus, report)

            # Process spaht temp control elements
            node = self.nodes["spaht"]
            htstatus = int(temp.find("htstatus").text)
            setting = int(temp.find("spaht").text)
            setPoint = int(temp.find("spasp").text)
            currentTemp = int(temp.find("spatemp").text)

            # Update node driver values
            node.setDriver("ST", currentTemp, report)
            node.setDriver("CLISPH", setPoint, report)
            node.update_thermo_drivers(setting, htstatus, report)


            # Iterate equipment child elements and process each
            for element in list(equipment):

                addr = element.tag
                state = element.text

                # Process elements that have a corresponding node
                if addr in self.nodes:

                    node = self.nodes[addr]
                    node.setDriver("ST", int(state), report)

    drivers = [
        {"driver": "ST", "value": 0, "uom": _ISY_BOOL_UOM},
        {"driver": "GV0" , "value": 1, "uom": _ISY_INDEX_UOM},
        {"driver": "GV1" , "value": 0, "uom": _ISY_INDEX_UOM},
        {"driver": "GV2" , "value": 0, "uom": _ISY_INDEX_UOM},
        {"driver": "GV3" , "value": 0, "uom": _ISY_INDEX_UOM},
        {"driver": "GV4" , "value": 0, "uom": _ISY_INDEX_UOM},
        {"driver": "GV5" , "value": 0, "uom": _ISY_INDEX_UOM},
        {"driver": "CLITEMP", "value": 0, "uom": _ISY_TEMP_F_UOM},
        {"driver": "GV9" , "value": 0, "uom": _ISY_TEMP_F_UOM}        
    ]
    commands = {"QUERY": query}

# Main function to establish Polyglot connection
if __name__ == "__main__":
    try:
        polyglot = polyinterface.Interface()
        polyglot.start()
        control = Controller(polyglot)
        control.runForever()
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
