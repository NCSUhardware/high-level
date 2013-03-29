#!/usr/bin/env python
"""Primary module of navigation package. 

As this develops, it will eventually accept a goalPose from planner, request a currentPose from localizer, then call
SBPL code (C++ made usable in Python by some method) and pass the configuration params. No file will be created and
no subprocess will spawn. The solution generated by SBPL will be returned to nav (not written to a file), and will then
be parsed and handed off to comm. Some additional logic involving issuing steps of the solution to comm, getting results,
checking for the amount of error, notifying localizer, maybe re-planning, and then issuing the next step will need to
be added."""

import logging.config
from collections import namedtuple
from subprocess import call
import os
from math import sqrt, sin, cos, pi
from datetime import datetime
import pprint as pp

# Movement objects for issuing macro or micro movement commands to nav. Populate and pass to qMove_nav queue.
macro_move = namedtuple("macro_move", ["x", "y", "theta", "timestamp"])
micro_move = namedtuple("micro_move", ["speed", "direction", "timestamp"])

# Dict of error codes and their human-readable names
errors = { 100 : "ERROR_BAD_CWD",  101 : "ERROR_SBPL_BUILD", 102 : "ERROR_SBPL_RUN", 103 : "ERROR_BUILD_ENV", 
  104 : "ERROR_BAD_RESOLUTION", 105 : "ERROR_SHORT_SOL", 106 : "ERROR_ARCS_DISALLOWED", 107 : "ERROR_DYNAMIC_DEM_UNKN", 108 :
  "ERROR_NO_CHANGE" }
errors.update(dict((v,k) for k,v in errors.iteritems())) # Converts errors to a two-way dict

# TODO These need to be calibrated
env_config = { "obsthresh" : "1", "cost_ins" : "1", "cost_cir" : "0", "cellsize" : "0.0015875", "nominalvel" : "1.0", 
  "timetoturn45" : "2.0" }

class Nav:

  def __init__(self, bot_loc, course_map, waypoints, qNav_loc, scNav, bot_state, qMove_nav, logger):
    """Setup navigation class

    :param bot_loc: Shared dict updated with best-guess location of bot by localizer
    :param course_map: Map of course
    :param waypoints: Locations of interest on the course
    :param qNav_loc: Multiprocessing.Queue object for passing movement feedback to localizer from navigator
    :param si: Serial interface object for sending commands to low-level boards
    :param bot_state: Dict of information about the current state of the bot (ex macro/micro nav)
    :param qMove_nav: Multiprocessing.Queue object for passing movement commands to navigation (mostly from Planner)
    :param logger: Used for standard Python logging
    """

    logger.info("Nav instantiated")

    # Store passed-in data
    self.bot_loc = bot_loc
    self.course_map = course_map
    self.waypoints = waypoints
    self.qNav_loc = qNav_loc
    self.scNav = scNav
    self.bot_state = bot_state
    self.qMove_nav = qMove_nav
    self.logger = logger
    self.logger.debug("Passed-in data stored to Nav object")

  def start(self, doLoop=True):
    """Setup nav here. Finds path from cwd to qwe directory and then sets up paths from cwd to required files. Opens a file
    descriptor for /dev/null that can be used to suppress output. Compiles SBPL using a bash script. Unless doLoop param is True,
    calls the inf loop function to wait on motion commands to be placed in the qMove_nav queue.

    :param doLoop: Boolean value that when false prevents nav from entering the inf loop that processes movement commands. This
    can be helpful for testing."""
    self.logger.info("Started nav")

    # Find path to ./qwe directory. Allows for flexibility in the location nav is run from.
    # TODO Could make this arbitrary by counting the number of slashes
    if os.getcwd().endswith("qwe"):
      path_to_qwe = "./"
    elif os.getcwd().endswith("qwe/navigation"):
      path_to_qwe = "../"
    elif os.getcwd().endswith("qwe/navigation/tests"):
      path_to_qwe = "../../"
    else:
      self.logger.critical("Unexpected CWD: " + str(os.getcwd()))
      return errors["ERROR_BAD_CWD"]

    # Setup paths to required files
    self.build_env_script = path_to_qwe + "../scripts/build_env_file.sh"
    self.build_sbpl_script = path_to_qwe + "navigation/build_sbpl.sh"
    self.sbpl_executable = path_to_qwe + "navigation/sbpl/cmake_build/bin/test_sbpl"
    self.env_file = path_to_qwe + "navigation/envs/env.cfg"
    self.mprim_file = path_to_qwe + "navigation/mprim/prim_tip_priority_6.299213e+002inch_step5" # FIXME Actually 0.0015875 res 
    self.map_file = path_to_qwe + "navigation/maps/binary_map.txt"
    self.sol_file = path_to_qwe + "navigation/sols/sol.txt"
    self.sol_dir = path_to_qwe + "navigation/sols"
    self.sbpl_build_dir = path_to_qwe + "navigation/sbpl/cmake_build"

    # Open /dev/null for suppressing SBPL output
    self.devnull = open("/dev/null", "w")
    self.logger.info("Opened file descriptor for writing to /dev/null")

    # Compile SBPL
    build_rv = call([self.build_sbpl_script, self.sbpl_build_dir])
    if build_rv != 0:
      self.logger.critical("Failed to build SBPL. Script return value was: " + str(build_rv))
      return errors["ERROR_SBPL_BUILD"]

    if doLoop: # Call main loop that will handle movement commands passed in via qMove_nav
      self.logger.debug("Calling main loop function")
      self.loop()
    else: # Don't call loop, return to caller 
      self.logger.info("Not calling loop. Individual functions should be called by the owner of this class object.")

  def genSol(self, goal_x, goal_y, goal_theta, env_config=env_config):
    """Use SBPL to generate a series of steps, within some set of acceptable motion primitives, that move the robot from the
    current location to the goal pose

    Eventually the SBPL code will be modified such that it can be called directly from here and params can be passed in-memory, to
    avoid file IP and spawning new processes.

    :param goal_x: X coordinate of goal pose
    :param goal_y: Y coordinate of goal pose
    :param goal_theta: Angle of goal pose
    :param env_config: Values used by SBPL in env.cfg file"""

    self.logger.debug("Generating plan")

    # Build environment file for input into SBPL
    # TODO Upgrade this to call SBPL directly, as described above
    # "Usage: ./build_env_file.sh <obsthresh> <cost_inscribed_thresh> <cost_possibly_circumscribed_thresh> <cellsize> <nominalvel>
    # <timetoturn45degsinplace> <start_x> <start_y> <start_theta> <end_x> <end_y> <end_theta> [<env_file> <map_file>]"
    self.logger.debug("env_config: " + "{obsthresh} {cost_ins} {cost_cir} {cellsize} {nominalvel} {timetoturn45}".format(**env_config))
    self.logger.debug("Current pose: " + str(self.bot_loc["x"]) + str(self.bot_loc["y"]) + str(self.bot_loc["theta"]))
    self.logger.debug("Goal pose: " + str(goal_x) + str(goal_y) + str(goal_theta))
    self.logger.debug("Map file: " + str(self.map_file))
    self.logger.debug("Environment file to write: " + str(self.env_file))

    build_env_rv = call([self.build_env_script, env_config["obsthresh"],
                                                env_config["cost_ins"],
                                                env_config["cost_cir"],
                                                env_config["cellsize"],
                                                env_config["nominalvel"],
                                                env_config["timetoturn45"],
                                                str(self.bot_loc["x"]),
                                                str(self.bot_loc["y"]),
                                                str(self.bot_loc["theta"]),
                                                str(goal_x),
                                                str(goal_y),
                                                str(goal_theta),
                                                str(self.env_file),
                                                str(self.map_file)])

    # Check results of build_env_script call
    if build_env_rv != 0:
      self.logger.critical("Failed to build env file. Script return value was: " + str(build_env_rv))
      return errors["ERROR_BUILD_ENV"]
    self.logger.info("Successfully built env file. Return value was: " + str(build_env_rv))

    # Run SBPL
    origCWD = os.getcwd()
    os.chdir(self.sol_dir)
    sbpl_rv = call([self.sbpl_executable, self.env_file, self.mprim_file])
    os.chdir(origCWD)

    # Check results of SBPL run
    if sbpl_rv == -6:
      self.logger.critical("Failed to run SBPL. SBPL return value was: " + str(sbpl_rv))
      return errors["ERROR_BAD_RESOLUTION"]
    if sbpl_rv < 0:
      self.logger.critical("Failed to run SBPL. SBPL return value was: " + str(sbpl_rv))
      return errors["ERROR_SBPL_RUN"]
    if sbpl_rv == 1:
      # No solution found
      self.logger.warning("SBPL failed to find a solution")
      return errors["NO_SOL"]
    self.logger.info("Successfully ran SBPL. Return value was: " + str(sbpl_rv))

    # Read solution file into memory and return it
    sol = []
    sol_lables = ["x", "y", "theta", "cont_x", "cont_y", "cont_theta"]
    for line in open(self.sol_file, "r").readlines():
      self.logger.debug("Read sol step: " + str(line).rstrip("\n"))
      sol.append(dict(zip(sol_lables, line.split())))
    self.logger.debug("Built sol list of dicts: " + pp.pformat(sol))

    return sol
      
  def loop(self):
    """Main loop of nav. Blocks and waits for motion commands passed in on qMove_nav"""

    self.logger.debug("Entering inf motion command handling loop")
    while True:
      # TODO Expand movement logic here
      self.logger.info("Blocking while waiting for command from queue with ID: " + pp.pformat(self.qMove_nav))
      move_cmd = self.qMove_nav.get()
      self.logger.info("Received move command: " + pp.pformat(move_cmd))

      if type(move_cmd) == macro_move:
        self.logger.info("Move command is if type macro")
        self.macroMove(x=move_cmd.x, y=move_cmd.y, theta=move_cmd.theta)
      elif type(move_cmd) == micro_move:
        self.logger.info("Move command is if type micro")
        self.microMove(speed=move_cmd.speed, direction=move_cmd.direction)
      else:
        self.logger.warn("Move command is of unknown type")

  def macroMove(self, x, y, theta):
    """Handle global movement commands. Accept a goal pose and use SBPL + other logic to navigate to that goal pose.

    :param x: X coordinate of goal pose
    :param y: Y coordinate of goal pose
    :param theta: Angle of goal pose"""
    # TODO This needs more logic
    self.logger.info("Handling macro move")

    while True:

      # Check if 'bot is at or close to the goal pose
      if atGoal(x, y, theta):
        self.logger.info("Macro move succeeded")
        return True #TODO Return difference between current pose and goal pose?

      # Generate solution
      self.logger.debug("macroMove requesting sol from ({}, {}, {}) to ({}, {}, {})".format(x, y, theta, self.bot_loc["x"], 
        self.bot_loc["y"], self.bot_loc["theta"]))
      sol = self.genSol(x, y, theta)

      # Handle value returned by genSol
      if type(sol) is not list:
        self.logger.info("macroMove did not receive a valid solution from genSol")

        # If no solution could be found
        if sol is errors["NO_SOL"]:
          self.logger.warn("No solution could be found, exiting macroMove")
          # TODO Notify planner or anyone who wants to know
          return errors[sol]
        elif sol in errors: # Some other type of error (likely more serious)
          self.logger.error("genSol returned " + errors[sol])
          return errors[sol]
        else:
          self.logger.error("Non-list, unknown-error returned to macroMove by genSol: " + str(sol))
          return sol
      else:
        self.logger.info("macroMove received a solution list from genSol")
        comm_sol_result = self.communicateSol(sol)

        if comm_sol_result is errors["ERROR_FAILED_MOVE"]:
          self.logger.warning("Attempted move wasn't within error margins, re-computing solution and trying again")
          continue
        elif comm_sol_result in errors:
          self.logger.error("Error while communicating sol to low-level code: " + errors[comm_sol_result])
          return comm_sol_result

  def communicateSol(self, sol):
    """Accept a solution list to pass to low-level code. Will pass commands to comm, wait for a response, and check if the
    response is within some error tolerance. If it isn't, a new goal will be generated. If it is, the next step will be passed to
    comm. Localizer will be updated at every return from comm.

    :param sol: List of dicts that contains a set of steps, using acceptable mprims, from the current pose to the goal pose
    """
    self.logger.debug("Communicating a solution to comm")

    if len(sol) <= 1:
      self.logger.warning("Don't know how to handle a solution with only " + str(len(sol)) + " steps.")
      return errors["ERROR_SHORT_SOL"]

    for i in range(1, len(sol)):

      # Only XY values or theta values should change, not both. This is because our mprim file disallows arcs. 
      XYxorT = self.XYxorTheta(sol[i-1], sol[i])
      if XYxorT is False:
        self.logger.error("XY values and theta values changed between steps, which can't happen without arcs.")
        return errors["ERROR_ARCS_DISALLOWED"]
      elif XYxorT in errors:
        self.logger.error("XYxorTheta failed with " + errors[XYxorT])
        return XYxorT

      dyn_dem = self.whichXYTheta(sol[i-1], sol[i])

      if dyn_dem in errors:
        self.logger.error("whichXYTheta failed with " + errors[dyn_dem])
        return dyn_dem

      if dyn_dem == "xy":
        self.logger.info("Movement will be in XY plane")

        # Calculate goal distance change in XY plane TODO May need to update once syntax GitHub issue is answered
        distance_m = sqrt((sol[i]["cont_x"] - sol[i-1]["cont_x"])**2 + (sol[i]["cont_y"] - sol[i-1]["cont_y"])**2)

        # Pass distance to comm and block for response
        commResult = self.scNav.botMove(distance_m) # TODO Confirm units

        # Report move result to localizer ASAP
        self.feedLocalizer(commResult)

        # Convert commResult to meters
        commResult_m = self.commResultToMeters(commResult)

        # Find difference between attempted move and reported result
        diffXY = self.diffMoveXY(commResult_m, distance_m)

        if diffXY <= errorMarginXY: # TODO Define errorMarginXY
          self.logger.info("Move was within error margin for moves in the XY plane")

          sol[i]["x"] = None
          sol[i]["y"] = None
          sol[i]["cont_x"] = (sin(self.radToDeg(sol[i]["cont_theta"])) * commResult_m) + sol[i]["cont_x"]
          sol[i]["cont_y"] = (cos(self.radToDeg(sol[i]["cont_theta"])) * commResult_m) + sol[i]["cont_y"]
        else:
          self.logger.warn("Move was greater than the error margin for moves in the XY plane")
          return errors["ERROR_FAILED_MOVE"]

      elif dyn_dem == "theta":
        self.logger.info("Movement will be in theta dimension")

        # Calculate goal change in theta TODO May need to update once syntax GitHub issue is answered
        angle_rads = sol[i]["cont_theta"] - sol[i-1]["cont_theta"]

        # Pass distance to comm and block for response
        commResult = self.scNav.botTurnRel(radians_to_degrees(angle_rads))

        # Report move result to localizer ASAP
        self.feedLocalizer(commResult)

        # Convert commResult to radians
        commResult_rads = self.commResultToRads(commResult)

        # Find difference between attempted move and reported result
        diffTheta = self.diffMoveTheta(commResult_rads, angle_rads)

        if diffTheta <= errorMarginTheta: # TODO Define errorMarginTheta
          self.logger.info("Move was within error margin for moves in the theta dimension")
          # TODO When off slightly, should I:
            # Assume that reported result is correct and base next move on that data (I guess so)
            # Base next move on ideal move, even those reported move was non-ideal (very-likely-no)
            # Report to localizer, wait for an updated position, then base next move on that (likely-no)

          # Update solution with actual move data so that future steps will base move on location reported by comm
          sol[i]["theta"] = None
          sol[i]["cont_theta"] = self.respThetaToCont(commResult_rads)
        else:
          self.logger.warn("Move was greater than the error margin for moves in the theta dimension")
          return errors["ERROR_FAILED_MOVE"]

      else:
        self.logger.error("Unknown whichXYTheta result: " + str(dyn_dem))
        return errors["ERROR_DYNAMIC_DEM_UNKN"]

  def commResultToMeters(self, commResult):
    """Converts the result returned by a call to comm.botMove to meters

    :param commResult: Raw result returned by comm.botMove"""
    # TODO Need to know the units of commResult

    return commResult

  def commResultToRads(self, commResult):
    """Converts the result returned by a call to comm.botTurn* to radians.

    :param commResult: Raw result returned by comm.botTurnRel or comm.botTurnAbs"""
    # TODO Need to know the format of commResult

    return commResult * pi / 180

  def diffMoveTheta(self, commResult_rads, goal_angle_rads):
    """Find the difference between the desired rotation in radians and the actual rotation in radians as reported by comm.
    This is a distinct function because it may become more complex in the future.

    :param commResult_rads: Angle rotated in radians as reported by comm.botTurnRel
    :param goal_angle_rads: Ideal angle to rotate by in radians, as passed to comm.botTurnRel"""

    return commResult_rads - goal_angle_rads

  def diffMoveXY(self, commResult_m, goal_dist_m):
    """Find the difference between the desired move distance in meters and the actual move distance in meters as reported by comm.
    This is a distinct function because it may become more complex in the future.

    :param commResult_m: Distance moved in meters as reported by comm.botMove
    :param goal_dist_m: Ideal distance to move in meters, as passed to comm.botMove"""

    return commResult_m - goal_dist_m

  def feedLocalizer(self, commResult):

    sensor_data = self.scNav.getAllSensorData()
    self.qNav_loc.put({"commResult" : commResult, "sensorData" : sensor_data, "timestamp" : datetime.now()}) # TODO Handle errors

  def XYxorTheta(self, step_prev, step_cur):
    """Check if the previous and current steps changed in the XY plane or the theta dimension, but not both.

    :param step_prev: The older of the two steps. This was the move executed during the last cycle (or the start position)
    :param step_cur: Current solution step being executed"""

    self.logger.debug("XYxorTheta step_prev is {} and step_cur is {}".format(pp.pformat(step_prev), pp.pformat(step_cur)))

    if step_prev["cont_x"] is not step_cur["cont_x"] or step_prev["cont_y"] is not step_cur["cont_y"]:
      if step_prev["cont_theta"] is not step_cur["cont_theta"]:
        self.logger.debug("Invalid: (X or Y) and theta changed")
        return False
      else:
        self.logger.debug("Valid: (X or Y) but not theta changed")
        return True
    elif step_prev["cont_theta"] is not step_cur["cont_theta"]:
      if step_prev["cont_x"] is not step_cur["cont_x"] or step_prev["cont_y"] is not step_cur["cont_y"]:
        self.logger.debug("Invalid: (X or Y) and theta changed")
        return False
      else:
        self.logger.debug("Valid: theta but not (X or Y) changed")
        return True
    else:
      self.logger.error("The previous and current steps have the same continuous values")
      return errors["ERROR_NO_CHANGE"]

  def whichXYTheta(self, step_prev, step_cur):
    """Find if movement is to be in the XY plane or the theta dimension. Assumes that XYxorTheta returns true on these params.

    :param step_prev: The older of the two steps. This was the move executed during the last cycle (or the start position)
    :param step_cur: Current solution step being executed"""

    if step_prev["x"] is None or step_prev["y"] is None:
      self.logger.debug("The previous move was in the XY plane")
      if step_prev["cont_theta"] is not step_cur["cont_theta"]:
        self.logger.debug("The previous step and current step involve a change in theta")
        return "theta"
      elif step_prev["cont_x"] is not step_cur["cont_x"] or step_prev["cont_y"] is not step_cur["cont_y"]:
        self.logger.debug("The previous step and the current step involve a change in XY")
        return "xy"
      else:
        self.logger.error("The previous and current steps have the same continuous values")
        return errors["ERROR_NO_CHANGE"]
    elif step_prev["theta"] is None:
      self.logger.debug("The previous move was in the theta dimension")
      if step_prev["cont_x"] is not step_cur["cont_x"] or step_prev["cont_y"] is not step_cur["cont_y"]:
        self.logger.debug("The previous step and the current step involve a change in XY")
        return "xy"
      elif step_prev["cont_theta"] is not step_cur["cont_theta"]:
        self.logger.debug("The previous step and current step involve a change in theta")
        return "theta"
      else:
        self.logger.error("The previous and current steps have the same continuous values")
        return errors["ERROR_NO_CHANGE"]
    else:
      self.logger.info("This should be the first move of this sol")
      if step_prev["cont_theta"] is not step_cur["cont_theta"]:
        self.logger.debug("The previous step and current step involve a change in theta")
        return "theta"
      elif step_prev["x"] is not step_cur["x"] or step_prev["y"] is not step_cur["y"]:
        self.logger.debug("The previous step and the current step involve a change in XY")
        return "xy"
      else:
        self.logger.error("The previous and current steps have the same continuous values")
        return errors["ERROR_NO_CHANGE"]

  def atGoal(self, x, y, theta, sig_figs=3):
    """Contains logic for checking if the current pose is the same as or within some acceptable tolerance of the goal pose

    :param x: X coordinate of goal pose
    :param y: Y coordinate of goal pose
    :param theta: Angle of goal pose"""

    self.logger.debug("Checking if goal pose reached")

    # Accept goal poses that are exacly correct
    if x == self.bot_loc["x"] and y == self.bot_loc["y"] and theta == self.bot_loc["theta"]:
      self.logger.info("Reached goal pose exactly")
      return True

    # Accept goal poses that are nearly correct
    if self.nearly_equal(x, self.bot_loc["x"], sig_figs) and self.nearly_equal(y, self.bot_loc["y"], sig_figs) \
                                               and self.nearly_equal(theta, self.bot_loc["theta"], sig_figs):
      self.logger.info("Reach goal pose to {} significant figures".format(sig_figs))
      return True

    # Rejct goal poses that are not correct
    self.logger.info("Have not reached goal pose")
    return False

  def nearly_equal(self, a, b, sig_fig=3):
    """Check if two numbers are equal to 5 sig figs
    Cite: http://goo.gl/iNDIS

    :param a: First number to compare
    :param b: Second number to compare
    :param sig_fig: Number of significant figures to compare them with"""
    return ( a==b or 
             int(a*10**(sig_fig-1)) == int(b*10**(sig_fig-1))
           )

  def microMove(self, speed, direction):
    """Handle simple movements on a small scale. Used for small adjustments by vision or planner when very close to objects.

    :param speed: Speed of bot during movement
    :param direction: Direction of bot travel during movement"""

    self.logger.debug("Handling micro move")
    # TODO This needs more logic


def run(bot_loc, course_map, waypoints, qNav_loc, scNav, bot_state, qMove_nav, logger=None):
  """Function that accepts initial data from controller and kicks off nav. Will eventually involve instantiating a class.

  :param bot_loc: Shared dict updated with best-guess location of bot by localizer
  :param course_map: Map of course
  :param waypoints: Locations of interest on the course
  :param qNav_loc: Multiprocessing.Queue object for passing movement feedback to localizer from navigator
  :param si: Serial interface object for sending commands to low-level boards
  :param bot_state: Dict of information about the current state of the bot (ex macro/micro nav)
  :param qMove_nav: Multiprocessing.Queue object for passing movement commands to navigation (mostly from Planner)
  """

  # Setup logging
  if logger is None:
    logging.config.fileConfig("logging.conf") # TODO This will break if not called from qwe. Add check to fix based on cwd?
    logger = logging.getLogger(__name__)
    logger.debug("Logger is set up")

  # Build nav object and start it
  logger.debug("Executing run function of nav")
  nav = Nav(bot_loc, course_map, waypoints, qNav_loc, scNav, bot_state, qMove_nav, logger)
  logger.debug("Built Nav object")
  return nav.start()
