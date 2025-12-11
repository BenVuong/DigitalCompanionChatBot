import os
import ffmpeg
class TTS:

    def __init__(self, client):
        self.client = client

    def chunk_text(self,text, max_chunk_size=500):
        """
        Split text into chunks at sentence boundaries without cutting words.
        Falls back to word boundaries if sentences are too long.
        """
        # Split into sentences
        sentences = []
        for delimiter in ['. ', '! ', '? ', '.\n', '!\n', '?\n']:
            text = text.replace(delimiter, delimiter + '<SPLIT>')
        
        raw_sentences = text.split('<SPLIT>')
        sentences = [s.strip() for s in raw_sentences if s.strip()]
        
        chunks = []
        current_chunk = ""
        
        for sentence in sentences:
            # If a single sentence is longer than max_chunk_size, split it by words
            if len(sentence) > max_chunk_size:
                # First, save current chunk if it has content
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""
                
                # Split long sentence by words
                words = sentence.split()
                temp_chunk = ""
                
                for word in words:
                    if len(temp_chunk) + len(word) + 1 <= max_chunk_size:
                        temp_chunk += (" " + word if temp_chunk else word)
                    else:
                        if temp_chunk:
                            chunks.append(temp_chunk.strip())
                        temp_chunk = word
                
                # Set current_chunk to the last piece of the long sentence
                if temp_chunk:
                    current_chunk = temp_chunk
            else:
                # Try to add sentence to current chunk
                test_chunk = current_chunk + (" " + sentence if current_chunk else sentence)
                
                if len(test_chunk) <= max_chunk_size:
                    current_chunk = test_chunk
                else:
                    # Current chunk is full, save it and start new one
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    current_chunk = sentence
        
        # Add the last chunk
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        return chunks
    
    async def generateStreaming(self, chunks, outputPath):
        os.makedirs(outputPath, exist_ok=True)
        temp_files = []

        for i, chunk in enumerate(chunks):
            temp_file = outputPath +f"/temp_audio_{i}.mp3"
            
            with self.client.audio.speech.with_streaming_response.create(
                model="global_preset",
                voice="chatterbox",
                input=chunk,
            ) as response:
                audio_data = response.read()
                with open(temp_file, "wb") as f:
                    f.write(audio_data)
            
            temp_files.append(temp_file)

            yield {
                "chunk_index": i,
                "total_chunks": len(chunks),
                "audio_file": f"temp_audio_{i}.mp3"
            }

        # await self.concatAudio(temp_files, f"{outputPath}/audio.mp3")
        
    async def concatAudio(self, temp_files, outputPath):
        try:
            inputs = [ffmpeg.input(f) for f in temp_files]
            join = ffmpeg.concat(*inputs, v=0, a=1)
            output = ffmpeg.output(join, outputPath)
            ffmpeg.run(output, overwrite_output=True, quiet=True)
            print("Audio concat success")
        except ffmpeg.Error as e:
            print(f"FFMPEG error: {e.stderr.decode()}")
            raise


    def generate(self, text_chunks, outputPath):
        os.makedirs("./static/tts", exist_ok=True)
        temp_files = []

        for i, chunk in enumerate(text_chunks):
            temp_file = outputPath +f"/temp_audio_{i}.mp3"
            
            with self.client.audio.speech.with_streaming_response.create(
                model="global_preset",
                voice="chatterbox-jeanette",
                input=chunk,
            ) as response:
                audio_data = response.read()
                with open(temp_file, "wb") as f:
                    f.write(audio_data)
            
            temp_files.append(temp_file)

        try:

            inputs = [ffmpeg.input(f) for f in temp_files]
            joined = ffmpeg.concat(*inputs, v=0, a=1)
            output = ffmpeg.output(joined, outputPath+"/audio.mp3")
            ffmpeg.run(output, overwrite_output=True, quiet=True)
            
            print("Audio concatenated successfully!")
            
        except ffmpeg.Error as e:
            print(f"FFmpeg error: {e.stderr.decode()}")
            raise

        for temp_file in temp_files:
            if os.path.exists(temp_file):
                os.remove(temp_file)