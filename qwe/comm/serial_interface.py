"""
Primary communication module to interact with motor and sensor controller board over serial.
"""

import sys
import signal
import json
import random
import serial
import threading
from multiprocessing import Process, Queue, Manager
from Queue import Empty as Queue_Empty
from time import sleep
from collections import namedtuple
import test

default_port = "/dev/ttyO3"
default_baudrate = 19200
default_timeout = 10  # seconds; float allowed
default_queue_maxsize = 10

default_speed = 200  # TODO set correct default speed when units are established
default_arm_ramp = 10  # 0-63; 10 is a good number
default_gripper_ramp = 5  # 0-63; 5 is a good number

max_command_id = 32767
command_eol = "\r"
is_sequential = False  # force sequential execution of commands
prefix_id = True  # send id pre-pended with commands?
servo_delay = 1.0  # secs.; duration to sleep after sending a servo command to let it finish (motor-controller returns immediately)
fake_delay = 0.001  # secs.; duration to sleep for when faking serial comm.

# TODO use direct commands [left, right]_[up, down, open, close, grab, drop]
Arm = namedtuple('Arm', ['name', 'arm_id', 'arm_angles', 'gripper_id', 'gripper_angles'])
left_arm = Arm("left", arm_id=0, arm_angles=(680, 310), gripper_id=1, gripper_angles=(900, 450))
right_arm = Arm("right", arm_id=2, arm_angles=(330, 710), gripper_id=3, gripper_angles=(0, 350))

sensors = { "heading": 0,  # compass / magnetometer
            "accel.x": 1,
            "accel.y": 2,
            "accel.z": 3,
            "ultrasonic.left": 4,
            "ultrasonic.front": 5,
            "ultrasonic.right": 6,
            "ultrasonic.back": 7 }

class SerialInterface(Process):
  """Encapsulates functionality to send (multiplexed) commands over a serial line."""
  
  def __init__(self, port=default_port, baudrate=default_baudrate, timeout=default_timeout, commands=None, responses=None):
    Process.__init__(self)
    
    self.port = port
    self.baudrate = baudrate
    self.timeout = timeout
    # NOTE Other default port settings: bytesize=8, parity='N', stopbits=1, xonxoff=0, rtscts=0
    self.device = None  # open serial port in run()
    self.live = False  # flag to signal threads
    self.fake_id = -1  # temp id used to make fake send-recv work
    
    # Create data structures to store commands and responses, unless passed in
    #self.sendLock = Lock()  # to prevent multiple processes from trying to send on the same serial line
    if commands is not None:
      self.commands = commands
    else:
      self.commands = Queue(default_queue_maxsize)  # internal queue to receive and service commands
    # TODO move queue out to separate class to manage it (and responses?)
    # TODO create multiple queues for different priority levels?
    
    if responses is not None:
      self.responses = responses
    else:
      self.manager = Manager()  # to facilitate process-safe shared memory, especially for responses; NOTE breaks on windows
      self.responses = self.manager.dict()  # a map structure to store responses by some command id
  
  def run(self):
    """Open serial port, and start send and receive threads."""
    # Open serial port
    try:
      self.device = serial.Serial(self.port, self.baudrate, timeout=self.timeout)  # open serial port
    except serial.serialutil.SerialException as e:
      print "SerialInterface.run(): Error: %s" % e
    
    # Flush input and output stream to clear any pending data; if port not available, fake it!
    if self.device is not None and self.device.isOpen():
      print "SerialInterface.run(): Serial port \"%s\" open (Baud rate: %d, timeout: %d secs.)" % (self.device.name, self.device.baudrate, (-1 if self.timeout is None else self.timeout))
      self.device.flushInput()
      self.device.flushOutput()
    else:
      print "SerialInterface.run(): Trouble opening serial port \"%s\"" % self.port
      print "SerialInterface.run(): Warning: Faking serial communications!"
      self.device = None  # don't quit, fake it
      self.send = self.fakeSend
      self.recv = self.fakeRecv
    
    # Set signal handler before starting communication loop (NOTE must be done in the main thread of this process)
    signal.signal(signal.SIGTERM, self.handleSignal)
    signal.signal(signal.SIGINT, self.handleSignal)
    
    if(is_sequential):
      # Start sequential execution (send + receive) thread
      print "SerialInterface.run(): Starting exec thread..."
      self.live = True
      
      self.execThread = threading.Thread(target=self.execLoop, name="EXEC")
      self.execThread.start()
      
      # Wait for thread to finish
      self.execThread.join()
      print "SerialInterface.run(): Exec thread joined."
    else:
      # Start send and receive threads
      print "SerialInterface.run(): Starting send and receive threads..."
      self.live = True
      
      self.sendThread = threading.Thread(target=self.sendLoop, name="SEND")
      self.sendThread.start()
      
      self.recvThread = threading.Thread(target=self.recvLoop, name="RECV")
      self.recvThread.start()
      
      # Wait for threads to finish
      self.sendThread.join()
      self.recvThread.join()
      print "SerialInterface.run(): Send and receive threads joined."
    
    # Reset signal handlers to default behavior
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    
    # Clean up: Clear queue; print warning if there are unserviced commands
    if not self.commands.empty():
      print "SerialInterface.run(): Warning: Terminated with pending command(s):"
      while not self.commands.empty():
        try:
          id, command = self.commands.get(False)  # do not block, in case some other thread/process has consumed commands in the meantime
          print "{id} {command}".format(id=id, command=command)
          # NOTE this also clears the queue (gets items until empty)
        except Queue_Empty:
          break
    self.commands = None
    
    # Clean up: Clear responses dict; print warning if there are unfetched responses
    if self.responses:
      print "SerialInterface.run(): Warning: Terminated with unfetched response(s):", ", ".join((str(id) + ": " + str(response) for id, response in self.responses.iteritems()))
      self.responses.clear()
    self.responses = None
    
    # Clean up: Close serial port
    if self.device is not None and self.device.isOpen():
      self.device.close()
      print "SerialInterface.run(): Serial port closed"
  
  def quit(self):
    self.commands.put((-1, "quit"))
  
  def handleSignal(self, signum, frame):
    if signum == signal.SIGTERM or signum == signal.SIGINT:
      print "SerialInterface.handleSignal(): Termination signal ({0}); stopping comm loop...".format(signum)
    else:
      print "SerialInterface.handleSignal(): Unknown signal ({0}); stopping comm loop anyways...".format(signum)
    self.quit()
  
  def sendLoop(self, block=True):
    """Monitor queue for commands and send them until signaled to quit."""
    print "SerialInterface.sendLoop(): [SEND-LOOP] Starting..."
    while self.live:
      try:
        (id, command) = self.commands.get(block)  # block=True waits indefinitely for next command
        if command == "quit":  # special "quit" command breaks out of loop
          self.live = False  # signal any other threads to quit as well
          break
        #print "[SEND-LOOP] Command :", command  # [debug]
        self.send(id, command)
      except Queue_Empty:
        #print "[SEND-LOOP] Warning: Empty queue (timeout?)"
        pass  # if queue is empty (after timeout, e.g.), simply loop back and wait for more commands
    print "SerialInterface.sendLoop(): [SEND-LOOP] Terminated."
  
  def recvLoop(self):
    """Listen for responses and collect them in internal dict."""
    print "SerialInterface.recvLoop(): [RECV-LOOP] Starting..."
    while self.live:
      try:
        response = self.recv()
        if response is None:  # None response means something went wrong, break out of loop
          print "[RECV-LOOP] Error: None response"
          break
        elif response:  # if response is not blank
          #print "[RECV-LOOP] Response:", response  # [debug]
          self.responses[response.get('id', -1)] = response  # store response by id for later retrieval, default id: -1
      except Exception as e:
        print "[RECV-LOOP] Error:", e
        break  # something wrong, break out of loop
    print "SerialInterface.recvLoop(): [RECV-LOOP] Terminated."
  
  def execLoop(self, block=True):
    """Combine functions of sendLoop() and recvLoop() to enforce sequential command execution."""
    print "SerialInterface.execLoop(): [EXEC-LOOP] Starting..."
    while self.live:
      try:
        (id, command) = self.commands.get(block)  # block=True waits indefinitely for next command
        if command == "quit":  # special "quit" command breaks out of loop
          self.live = False  # signal any other threads to quit as well
          break
        response = self.execute(id, command)
        if response is None:  # None response means something went wrong, break out of loop
          print "[EXEC-LOOP] Error: None response"
          break
        elif response:  # if response is not blank
          #print "[EXEC-LOOP] Response:", response  # [debug]
          self.responses[response.get('id', id)] = response  # store response by id for later retrieval, default id: what was passed in
      except Queue_Empty:
        print "[EXEC-LOOP] Warning: Empty queue (timeout?)"
        pass  # if queue is empty (after timeout, e.g.), simply loop back and wait for more commands
      except Exception as e:
        print "[EXEC-LOOP] Error:", e
        break  # something wrong, break out of loop
    print "SerialInterface.execLoop(): [EXEC-LOOP] Terminated."
  
  def send(self, id, command):
    """Send a command, adding terminating EOL char(s)."""
    try:
      if prefix_id:
        command = str(id) + ' ' + command
      print "[SEND] {command}".format(command=command)  # [debug]
      self.device.write(command + command_eol)  # add EOL char(s)
      return True
    except Exception as e:
      print "[SEND] Error:", e
      return False
  
  def recv(self):
    """Receive a newline-terminated response, and return it as a dict."""
    try:
      responseStr = self.device.readline()  # NOTE response must be \n terminated
      responseStr = responseStr.strip()  # strip EOL
      if len(responseStr) == 0:
        #print "[RECV] Warning: Blank response (timeout?)"
        return { }  # return a blank dict
      else:
        print "[RECV] {0}".format(responseStr)  # [debug]
        response = json.loads(responseStr)
        return response  # return dict representation of JSON object
    except Exception as e:
      print "[RECV] Error:", e
      return None
  
  def execute(self, id, command):
    """Send a command, wait for response and return it."""
    try:
      self.send(id, command)
      response = self.recv()
      #print "[EXEC] {0}".format(response)  # [debug]
      while response is not None and not response and self.live:
        response = self.recv()  # wait till a non-None, non-blank-dict response is received; TODO wait only a fixed number of times?
      return response
    except Exception as e:
      print "[EXEC] Error:", e
      return None
  
  def fakeSend(self, id, command):
    if prefix_id:
      command = str(id) + ' ' + command
      self.fake_id = id
    print "[FAKE-SEND] {command}".format(command=command)
    sleep(fake_delay)
    return True
  
  def fakeRecv(self):
    ctr = 0
    while self.fake_id == -1 and ctr < 10:
      sleep(self.timeout / 10)
      ctr = ctr + 1
    
    if self.fake_id == -1:
      #print "[FAKE-RECV] Warning: Blank response (timeout?)"
      return { }
    
    sleep(fake_delay)
    response = { 'result': True, 'msg': "", 'id': self.fake_id }  # put in the last id that fakeSend() got
    self.fake_id = -1
    print "[FAKE-RECV] {response}".format(response=response)
    return response


class SerialCommand:
  """Exposes a set of methods for sending different navigation, action and sensor commands via a SerialInterface."""
  
  def __init__(self, commands, responses):
    self.commands = commands   # shared Queue
    self.responses = responses  # shared dict
  
  def putCommand(self, command):  # priority=0
    """Add command to queue, assigning a unique identifier."""
    id = random.randrange(max_command_id)  # generate unique command id
    self.commands.put((id, command))  # insert command into queue as 2-tuple (id, command)
    # TODO insert into appropriate queue by priority?
    return id  # return id
  
  def getResponse(self, id, block=True):
    """Get response for given id from responses dict and return."""
    if block:
      while not id in self.responses:
        pass  # if blocking, wait till command has been serviced
    elif not id in self.responses:
      return None  # if non-blocking and command hasn't been serviced, return None
    
    response = self.responses.pop(id)  # get response and remove it
    return response
  
  def runCommand(self, command):
    """Add command to queue, block for response and return it."""
    id = self.putCommand(command)
    response = self.getResponse(id)
    return response
  
  def quit(self):
    """Terminate threads and quit."""
    self.putCommand("quit")  # special command "quit" is not serviced, it simply terminates the send and receive thread(s)
  
  def botStop(self):
    """Stop immediately."""
    response = self.runCommand("stop")
    return response.get('result', False)
  
  def botPWMDrive(self, left, right):
    """Set individual wheel/side speeds (units: PWM values 0 - 10000)."""
    response = self.runCommand("pwm_drive {left} {right}".format(left=left, right=right))
    return response.get('result', False)
    
  def botSet(self, distance, angle, speed=default_speed):  # distance: mm (?), angle: 10ths of a degree, speed: encoder units (200-1000, default: 400)
    """Move distance and turn to given absolute angle simultaneously."""
    response = self.runCommand("set {angle} {speed} {distance}".format(angle=angle, speed=speed, distance=distance))
    return int(response.get('distance', distance)), int(response.get('absHeading', angle))  # return 2-tuple (<actual distance>, <abs. heading>)
  
  def botMove(self, distance, speed=default_speed):  # distance: mm (?), speed: encoder units (200-1000, default: 400)
    response = self.runCommand("move {speed} {distance}".format(speed=speed, distance=distance))
    return int(response.get('distance', distance))
  
  def botFollow(self, distance, speed=default_speed, which=0):  # distance: mm (?), speed: encoder units (200-1000, default: 400), which = 1 (left), 2 (right)
    response = self.runCommand("follow {speed} {distance} {which}".format(speed=speed, distance=distance, which=which))
    return int(response.get('distance', distance))
  
  def botTurnAbs(self, angle):  # angle: 10ths of a degree
    response = self.runCommand("turn_abs {angle}".format(angle=int(angle)))
    return response.get('absHeading', angle)
  
  def botTurnRel(self, angle):  # angle: 10ths of a degree
    response = self.runCommand("turn_rel {angle}".format(angle=int(angle)))
    return (angle - response.get('headingErr', 0))  # turn_rel returns remaining heading error, i.e. desired - actual
  
  def armSetAngle(self, arm_id, angle, ramp=default_arm_ramp):
    response = self.runCommand("servo {channel} {ramp} {angle}".format(channel=arm_id, ramp=ramp, angle=angle))
    sleep(servo_delay)  # wait here for servo to reach angle
    return response.get('result', False)
  
  def armUp(self, arm):
    # TODO switch to [left/right]_up when ready
    #response = self.runCommand(arm.name + "_up")
    #return response.get('result', False)
    return self.armSetAngle(arm.arm_id, arm.arm_angles[0])
  
  def armDown(self, arm):
    # TODO switch to [left/right]_down when ready
    #response = self.runCommand(arm.name + "_down")
    #return response.get('result', False)
    return self.armSetAngle(arm.arm_id, arm.arm_angles[1])
  
  def gripperSetAngle(self, gripper_id, angle, ramp=default_gripper_ramp):
    response = self.runCommand("servo {channel} {ramp} {angle}".format(channel=gripper_id, ramp=ramp, angle=angle))
    sleep(servo_delay)  # wait here for servo to reach angle
    return response.get('result', False)
  
  def gripperOpen(self, arm):
    # TODO switch to [left/right]_open when ready
    #response = self.runCommand(arm.name + "_open")
    #return response.get('result', False)
    return self.gripperSetAngle(arm.gripper_id, arm.gripper_angles[0])
  
  def gripperClose(self, arm):
    # TODO switch to [left/right]_close when ready
    #response = self.runCommand(arm.name + "_close")
    #return response.get('result', False)
    return self.gripperSetAngle(arm.gripper_id, arm.gripper_angles[1])
  
  def armPick(self, arm):
    response = self.runCommand(arm.name + "_pick")
    return response.get('result', False)
  
  def armDrop(self, arm):
    response = self.runCommand(arm.name + "_drop")
    return response.get('result', False)
  
  def getAllSensorData(self):
    return self.runCommand("sensors")  # return the entire dict full of sensor data
  
  def getSensorData(self, sensorId):
    """Fetches current value of a sensor. Handles only scalar sensors, i.e. ones that return a single int value."""
    response = self.runCommand("sensor {sensorId}".format(sensorId=sensorId))
    # TODO timestamp sensor data here?
    return int(response.get('data', -1))  # NOTE this only handles single-value data
  
  def getSensorDataByName(self, sensorName):
    sensorId = sensors.get(sensorName, None)
    if sensorId is not None:
      return self.getSensorValue(sensorId)
    else:
      return -1
  
  def compassReset(self):
    response = self.runCommand("compass_reset")
    return response.get('result', False)
  
  # TODO write specialized sensor value fetchers for non-scalar sensors like the accelerometer (and possibly other sensors for convenience)


def main():
  """
  Standalone testing program for SerialInterface.
  Usage:
    python serial_interface.py [port [baudrate [timeout]]]
  """
  # Parameters
  port = default_port
  baudrate = default_baudrate
  timeout = default_timeout
  
  if len(sys.argv) > 1:
    port = sys.argv[1]
    if len(sys.argv) > 2:
      baudrate = int(sys.argv[2])
      if len(sys.argv) > 3:
        timeout = None if sys.argv[3] == "None" else float(sys.argv[3])
  
  # Serial interface
  print "main(): Creating SerialInterface(port=\"{port}\", baudrate={baudrate}, timeout={timeout}) process...".format(port=port, baudrate=baudrate, timeout=(-1 if timeout is None else timeout))
  
  manager = Manager()  # manager service to share data across processes; NOTE must on Windows
  si_commands = Queue(default_queue_maxsize)  # queue to store commands, process-safe; NOTE must on Windows
  si_responses = manager.dict()  # shared dict to store responses, process-safe; NOTE must on Windows
  si = SerialInterface(port, baudrate, timeout, si_commands, si_responses)  # NOTE commands and responses need not be passed in (other than in Windows?); SerialInterface creates its own otherwise
  #si = SerialInterface(port, baudrate, timeout)
  si.start()
  
  # Serial command(s): Wrappers for serial interface
  sc1 = SerialCommand(si.commands, si.responses)  # pass in shared commands and responses structures to create a SerialCommand wrapper object
  # NOTE pass this SerialCommand object to anything that needs to call high-level methods (botMove, botTurn*, getSensorData, etc.)
  sc2 = SerialCommand(si.commands, si.responses) # multiple SerialCommand objects can be created if needed; the underlying SerialInterface data structures are process- and thread- safe
  
  # Set signal handlers
  def handleSignal(signum, frame):
    if signum == signal.SIGTERM or signum == signal.SIGINT:
      print "handleSignal(): Termination signal ({0}); stopping comm loop...".format(signum)
    else:
      print "handleSignal(): Unknown signal ({0}); stopping comm loop anyways...".format(signum)
    si.quit()
  
  signal.signal(signal.SIGTERM, handleSignal)
  signal.signal(signal.SIGINT, handleSignal)
  
  # Test sequence, non-interactive
  print "main(): Starting test sequence...\n"
  sc1.botStop()
  pTest = Process(target=test.testPoly, args=(sc1,))
  pTest.start()  # start test process
  
  # Interactive session
  print "main(): Starting interactive session [Ctrl+D or \"quit\" to end]...\n"
  while True:
    try:
      command = raw_input("Me    > ")  # input command from user
    except (EOFError, KeyboardInterrupt):
      command = "quit"
    
    if command == "quit":
      print "\nmain(): Quiting interactive session..."
      break
    
    response = sc2.runCommand(command)  # equiv. to putCommand()..getResponse()
    #id = sc2.putCommand(command)
    #response = sc2.getResponse(id)
    print "Device: {response}".format(response=response)
  print "main(): Interactive session terminated."
  
  # Reset signal handlers to default behavior
  signal.signal(signal.SIGTERM, signal.SIG_DFL)
  signal.signal(signal.SIGINT, signal.SIG_DFL)
  
  # Clean-up
  sc1.botStop()  # stop bot, if moving
  pTest.join()  # wait for test process to join
  print "main(): Test sequence terminated."
  
  # Wait for SerialInterface to terminate
  sc2.quit()
  si.join()
  print "main(): Done."


if __name__ == "__main__":
  main()
