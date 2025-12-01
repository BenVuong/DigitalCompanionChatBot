import os
import json
toolset = [
    {
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
]

def writeDeveloperPrompt(developerPrompt: str):
    """
    This tool will log a developer message that will be picked up by another llm chatbot for processing
    Arguments
      developerPrompt: the developer prompt that will logged. It should be formatted like you see (observation from image here) respond to the user about the observation as a natural companion speech"
    """
    filename= "pending_prompt.json"
    try:
        if os.path.exists(filename):
            with open(filename, "r") as f:
                data = json.load(f)
                prompts_queue = data.get("prompts", [])
        else:
            prompts_queue = []
        
        # Add new prompt to the queue
        prompts_queue.append({
            "developerPrompt": developerPrompt
        })
        
        # Write updated queue back to file
        with open(filename, "w") as f:
            json.dump({"prompts": prompts_queue}, f, indent=2)
        
        return "Function executed successfully"
        
    except Exception as e:
        print(f"Failed to write prompt file: {e}")
        return (f"Failed to write prompt file: {e}")
        