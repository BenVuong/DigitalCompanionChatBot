from openai import OpenAI
import json
import base64
import cv2
cap = cv2.VideoCapture(0)
from turntable import TurnTable
from tools import writeDeveloperPrompt
client = OpenAI(api_key="none", base_url="http://localhost:5001/v1")
turntable = TurnTable("COM6")

# Define the function schema for OpenAI
tools = [
  {
    "type": "function",
    "function": {
      "name": "turnCameraLeft90",
      "description": "Turns the camera 90 degrees to the left",
      "parameters": {
        "type": "object",
        "properties": {},
        "required": []
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "turnCameraRight90",
      "description": "Turns the camera 90 degrees to the right",
      "parameters": {
        "type": "object",
        "properties": {},
        "required": []
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "writeDeveloperPrompt",
      "description": "Logs a developer message that will be picked up by another LLM chatbot for processing.",
      "parameters": {
        "type": "object",
        "properties": {
          "developerPrompt": {
            "type": "string",
            "description": "The developer prompt to be logged. Format example: '(observation from image here) respond to the user about the observation as a natural companion speech.'"
          }
        },
        "required": ["developerPrompt"]
      }
    }
  }
]

systemPrompt = """
You are a visual observer controlling a camera on a turntable. Your job is to:

1. When you receive a new image if you think it is interesting and something that you would like to point out the user then use writeDeveloperPrompt to describe what you see (start with "you see ")
2. When you use writeDeveloperPrompt, only use it when you see something interesting, for example if you just see a blank wall don't use the writeDeveloperPrompt tool
4. After writing your observation, you MUST turn the camera (left or right) to get a new angle

Important: Look at the conversation history. If you've already written a prompt for the current image, just turn the camera. If you've just turned the camera, wait for the next image.
"""

# Persistent message history across all loops
conversation_history = [{"role": "system", "content": systemPrompt}]

def chat_with_functions(image: str, messages: list):
    """Handle a conversation with function calling capability."""
    
    # Add the new image to the conversation
    messages.append({
        "role": "developer",
        "content": [
            {
                "type": "text",
                "text": "Here's the current view. If this current view is interesting and worth taking note for the user, then use the writeDeveloperPrompt tool. If the current view is not intreasting, for example like a blank wall, the turn the camera left or right to get a new view"
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{image}"
                }
            }
        ]
    })
    
        # API call - let the model decide what to do
    response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )
        
    response_message = response.choices[0].message
    messages.append(response_message)
        
        # Check if the model wants to call a function
    if response_message.tool_calls:
            # Process each tool call
            for tool_call in response_message.tool_calls:
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)
                
                print(f"🔧 Model wants to call function: {function_name}")
                print(f"📝 Arguments: {function_args}")
                
                # Call the actual function
                if function_name == "turnCameraLeft90":
                    function_response = turntable.turnCameraLeft90(**function_args)
                    
                elif function_name == "turnCameraRight90":
                    function_response = turntable.turnCameraRight90(**function_args)
  
                elif function_name == "writeDeveloperPrompt":
                    function_response = writeDeveloperPrompt(**function_args)
                else:
                    function_response = json.dumps({"error": "Unknown function"})
                
                print(f"📊 Function response: {function_response}\n")
                
                # Add the function response to messages
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": function_response
                })
            
      
            # Otherwise, prompt the model to continue
            messages.append({
                "role": "user",
                "content": "Good! Now turn the camera left or right to get a new angle."
            })
    else:
            # No function call - prompt it to take action
            print("⚠️ Model didn't call any function. Prompting it to act...")
            messages.append({
                "role": "user",
                "content": "Please use the tools available to you. First describe what you see, then turn the camera."
            })
    

    
    return messages

# Example usage
if __name__ == "__main__":
    print("=" * 60)
    print("OpenAI Function Calling Demo with Memory")
    print("=" * 60 + "\n")
    
    
    while True:
      
        userInput = input("Enter: ")
        print(f"\n{'='*60}")
        # print(f"LOOP {x + 1}")
        print(f"{'='*60}")
        
        ret, frame = cap.read()
    
        if not ret:
            print("Error: Failed to capture frame.")
            break
        
        # Display the frame
        cv2.imshow('Webcam Feed', frame)
        cv2.waitKey(1)  # Add small delay to show frame
        
        # Encode the frame as JPEG
        _, buffer = cv2.imencode('.jpg', frame)
        
        # Convert to base64
        image_base64 = base64.b64encode(buffer).decode('utf-8')
        
        print("Image captured! Sending to LLM for analysis...")
        
        try:
            # Send to OpenAI API with persistent conversation history
            conversation_history = chat_with_functions(
                image=image_base64, 
                messages=conversation_history
            )
            
            # print(f"\n✅ Loop {x + 1} complete. Message history length: {len(conversation_history)}")
            
        except Exception as e:
            print(f"Error calling OpenAI API: {e}")
    
    cap.release()
    cv2.destroyAllWindows()
    print("\n" + "="*60)
    print("Demo completed!")
    print("="*60)