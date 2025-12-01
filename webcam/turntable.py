import serial
import time
#angles
#512 steps - 360 degrees
#128 steps - 90 degrees

class TurnTable:
    def __init__(self, port):
        self.ser = serial.Serial(
            port=port,       
            baudrate=9600,
            timeout=1
        )
        time.sleep(2) 
        print("Connected to Arduino")
        print("---------------------")


    def send_command(self, cmd):
        print(f"\nSending: {cmd}")

        self.ser.write((cmd + "\n").encode()) 

        
        while True:
            line = self.ser.readline().decode().strip()

            if line:
                print("Arduino:", line)

                if line == "DONE":
                    print("✓ Movement complete")
                    break  

    def turnCameraLeft90(self):
        """This function turns the camera 90 degrees to the left"""
        self.send_command("F128")
        return("Camera turned 90 degrees to the left. Function executed Sucessfully.")

    def turnCameraRight90(self):
        """This function turns the camera 90 degrees to the right"""
        self.send_command("R128")
        return("Camera turned 90 degrees to the right. Function executed Sucessfully.")

    
    def closeConnection(self):
        self.ser.close()

