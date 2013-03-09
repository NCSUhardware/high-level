"""
Primary communication module to interact with motor and sensor controller board over serial.
"""

import sys
import random
import serial
import threading
import Queue

class SerialInterface:
  """
  Encapsulates functionality to send (multiplexed) commands over a serial line.
  Exposes a set of methods for different navigation, action and sensor commands.
  """
  PORT = "/dev/ttyO3"
  BAUDRATE = 57600
  TIMEOUT = 1  # seconds; float allowed
  
  QUEUE_MAXSIZE = 10
  
  def __init__(self, port=PORT, baudrate=BAUDRATE, timeout=TIMEOUT):
    self.port = port
    self.baudrate = baudrate
    self.timeout = timeout
    # NOTE Other default port settings: bytesize=8, parity='N', stopbits=1, xonxoff=0, rtscts=0
    self.device = None  # open serial port in start()
    
    self.commands = Queue.Queue(SerialInterface.QUEUE_MAXSIZE)  # internal queue to receive and service commands
    # TODO create multiple queues for different priority levels?
    self.responses = { }  # a map structure to store responses by commandId
  
  def start(self):
    """Open serial port and start main loop thread/process."""
    try:
      self.device = serial.Serial(self.port, self.baudrate, timeout=self.timeout)  # open serial port
    except serial.serialutil.SerialException as e:
      print "SerialInterface.start(): Error: %s" % e
      return False
    
    if self.device.isOpen():
      print "SerialInterface.start(): Serial port \"%s\" open" % self.device.name
    else:
      print "SerialInterface.start(): Unspecified error opening serial port \"%s\"" % self.port
      return False
    
    self.loopThread = threading.Thread(target=self.loop)
    self.loopThread.start()
    # TODO use multiprocessing and multiprocessing.Queue instead of threading and Queue
    
    return True
  
  def loop(self):
    """Main loop: Monitor queue for commands and service them until signaled to quit."""
    print "SerialInterface.loop(): Starting main [LOOP]..."
    while True:
      try:
        (commandId, command) = self.commands.get(True)  # blocks indefinitely
        if command == "quit":  # special "quit" command breaks out of loop
          break
        #print "[LOOP] Command : " + command
        
        response = self.execCommand(command)
        if response is None:  # None response means something went wrong, break out of loop
          break
        #print "[LOOP] Response: " + response
        
        self.responses[commandId] = command  # store result by commandId for later retrieval
      except Queue.Empty:
        print "[LOOP] Empty queue"
        pass  # if queue is empty, simply loop back and wait for more commands
    
    # Clean up: Close serial port
    print "SerialInterface.loop(): Main loop terminated"
    if self.device is not None and self.device.isOpen():
      self.device.close()
      print "SerialInterface.loop(): Serial port closed"
    
    # Clean up: Clear queue and responses dict (print warning if there are unserviced commands?)
    if not self.commands.empty():
      print "SerialInterface.loop(): Warning: Terminated with pending commands"
    self.commands = None  # TODO find a better way to simply clear the queue (get items until empty?)
    self.responses.clear()
  
  def stop(self):
    """Stop main loop by sending quit command."""
    self.putCommand("quit")
  
  def execCommand(self, command):
    """Execute command (send over serial port) and return response"""
    try:
      self.device.write(command + "\n")  # NOTE '\n' terminated command
      response = self.device.readline()  # NOTE '\n' terminated response
      return response
    except Exception as e:
      print "SerialInterface.execCommand(): Error: " + e
      return None
  
  def putCommand(self, command):  # priority=0
    commandId = random.randrange(sys.maxint)  # generate unique command ID
    self.commands.put((commandId, command))  # insert command into queue as 2-tuple (ID, command)
    # TODO insert into appropriate queue by priority?
    return commandId  # return ID
  
  def getResponse(self, commandId, block=True):
    if block:
      while not commandId in self.responses:
        pass  # if blocking, wait till command has been serviced
    elif not commandId in self.responses:
      return None  # if non-blocking and command hasn't been serviced, return None
    
    response = self.responses.pop(commandId)  # get response and remove it
    return response
  
  def botSetWheel(self, wheelId, value):
    pass  # TODO set individual wheel velocity to value (PWM units?)
  
  def botStop(self):
    pass  # TODO stop all wheels
  
  def botMove(self, distance):
    pass  # TODO send move command, wait for completion ack, return actual distance traveled (relative)
  
  def botTurn(self, angle):
    pass  # TODO send turn command, wait for completion ack, return actual angle turned (relative)
  
  def armRotate(self, armId, angle):
    pass  # TODO send arm rotate command, wait for completion ack, return actual arm angle (absolute?)
  
  def armDown(self, armId):
    pass  # TODO rotate arm to lowest position (to pick-up blocks) [use armRotate], return True/False to indicate success/failure
  
  def armUp(self, armId):
    pass  # TODO rotate arm to highest position (e.g. with block in gripper) [use armRotate], return True/False to indicate success/failure
  
  def gripperSet(self, armId, value):
    pass  # TODO open gripper to specified value (distance/angle) and return actual value on completion
  
  def gripperClose(self, armId):
    pass  # TODO close gripper (to grab) [use gripperSet] and return True/False on completion
  
  def gripperOpen(self, armId):
    pass  # TODO open gripper (to release) [use gripperSet] and return True/False on completion
  
  def getSensorData(self, sensorId):
    pass  # TODO obtain data for given sensorId and return value


def main():
  """
  Standalone testing program for SerialInterface.
  Usage:
    python serial_interface.py [port [baudrate [timeout]]]
  """
  port = SerialInterface.PORT
  baudrate = SerialInterface.BAUDRATE
  timeout = SerialInterface.TIMEOUT
  
  if len(sys.argv) > 1:
    port = sys.argv[1]
    if len(sys.argv) > 2:
      baudrate = int(sys.argv[2])
      if len(sys.argv) > 3:
        timeout = None if sys.argv[3] == "None" else float(sys.argv[3])
  
  print "main(): Creating SerialInterface(port=\"{port}\", baudrate={baudrate}, timeout={timeout})".format(port=port, baudrate=baudrate, timeout=timeout)
  serialInterface = SerialInterface(port, baudrate, timeout)
  if not serialInterface.start():
    return
  
  print "main(): Starting interactive session [Ctrl+D or \"quit\" to end]...\n"

  while True:
    try:
      command = raw_input("Me    : ")  # input command from user
    except EOFError:
      command = "quit"
    
    commandId = serialInterface.putCommand(command)
    if command == "quit":
      print "\nmain(): Exiting interactive session..."
      break
    
    response = serialInterface.getResponse(commandId)
    print "Device: " + response + " [" + str(commandId) + "]"
  
  #serialInterface.stop()
  print "main(): Done."


if __name__ == "__main__":
  main()