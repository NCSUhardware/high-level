from numpy import pi
from sensors import Ultrasonic
from pose import Pose

# datasheets say resolution is within 0.1"
# need to figure out how this translates to gaussian std dev
sensor_noise = 0.05  # 2 std devs = resolution, so 95% of readings within resolution

centered_str = []
centered_str.append(Ultrasonic('F', Pose(0.0,0,0.0), sensor_noise))
centered_str.append(Ultrasonic('L', Pose(0.0,0.0,pi/2), sensor_noise))
centered_str.append(Ultrasonic('R', Pose(0.0,0.0,-pi/2), sensor_noise))
centered_str.append(Ultrasonic('B', Pose(0.0,0.0,-pi), sensor_noise))

centered_cone = []
centered_cone.append(Ultrasonic('F', Pose(0.0,0,0.0), sensor_noise, cone=True))
centered_cone.append(Ultrasonic('L', Pose(0.0,0.0,pi/2), sensor_noise, cone=True))
centered_cone.append(Ultrasonic('R', Pose(0.0,0.0,-pi/2), sensor_noise, cone=True))
centered_cone.append(Ultrasonic('B', Pose(0.0,0.0,-pi), sensor_noise, cone=True))

offset = []
offset.append(Ultrasonic('F', Pose(4.0,0,0.0), sensor_noise))
offset.append(Ultrasonic('L', Pose(0.0,-4.0,pi/2), sensor_noise))
offset.append(Ultrasonic('R', Pose(0.0,4.0,-pi/2), sensor_noise))
offset.append(Ultrasonic('B', Pose(-4.0,0,-pi), sensor_noise))

default = centered_cone
