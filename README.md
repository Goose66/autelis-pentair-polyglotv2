# autelis-pentair-polyglotv2
A Nodeserver for Polyglot v2 that interfaces with the Autelis Pool Control (Pentair) device to allow the ISY 994i to control Pentair Intellitouch/EasyTouch/SunTouch pool systems. See http://www.autelis.com/ for more information on the Autelis Pool Control device.

Instructions for Local (Co-resident with Polyglot) installation:

1. Copy the files from this repository to the folder ~/.polyglot/nodeservers/Autelis-Pentair in your Polyglot v2 installation.
2. Log into the Polyglot Version 2 Dashboard (https://(Polyglot IP address):3000)
3. Add the Autelis-Pentair nodeserver as a Local nodeserver type.
4. Add the following required Custom Configuration Parameters under Configuration:
```
    ipaddress - IP address of Autelis Pool Control device 
    username - login name for Autelis Pool Control device
    password - password for Autelis Pool Control device
```
5. Add the following optional Custom Configuration Parameters:
```
    pollinginterval - polling interval in seconds (defaults to 20)
```
Here are the known issues with this version:

1. The nodes are added with the node address as the name (description). You need to change the names (especially for the circuits and features) to the name of the pool device controlled by the node.
2. The equipment nodes only take DON and DOF commands, so if you put the nodes in a Managed Scene and do a Fast On or Fast Off, the node will not respond.
3. The Nodeserver only adds nodes for circuits and features that return values (along with "poolht" node and "spaht" node), so it should only add nodes for those equipment specific to your installation.
4. The Nodeserver currently doesn't support dimming circuits/features, colored lights, or variable pump speeds.
5. The Nodeserver utilizes whatever temp units (F or C) are set in your Pentair controller. If you change it while the Nodeserver is running, everything will update, but temp values can be wonky for a while. A Query (or time) should restore correct values.