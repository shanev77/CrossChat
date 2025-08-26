AI Cross-Chat GUI (Bob ↔ Jane)

A desktop GUI application that lets you run two Ollama servers as conversational agents (e.g., Bob on AIHub and Jane on NODE01) and watch them debate, discuss, or collaborate on any topic you choose.
Instead of juggling terminal commands, this tool provides a clean interface with buttons, text fields, and live conversation output.

✨ Features

Two-Bot Conversations
Pick any two Ollama models and let them chat back and forth automatically.
GUI Interface (Tkinter)
Connect to AIHub and NODE01 by entering their URLs.
Fetch available models with a single click.
Drop-down menus to select Bob’s model and Jane’s model.
Manual Download Model button to pull missing models on the fly.
Customizable Parameters
Topic input box to steer the discussion.
Number of turns.
Temperature (creativity).
Delay between turns.
Num Predict (max tokens per reply).
Retry/Backoff handling for timeouts.
History window (how much chat context to keep).
Transcript Logging
Save the entire conversation automatically to a timestamped .txt file (or custom path).
Graceful Error Handling
Retries and backoff on timeouts.
Friendly error messages instead of stack traces.
Conversation stops cleanly if one side fails.
Extensible Personas
Default names are Bob (AIHub) and Jane (NODE01).
Natural introductions — avoids AI “I’m a model trained on…” talk.

🚀 Getting Started

Prerequisites

Python 3.9+

pip install requests

Clone and Run
git clone https://github.com/yourusername/ai-crosschat-gui.git
cd ai-crosschat-gui
python crosschat_gui.py

🖥 Usage

Start your Ollama servers on AIHub and NODE01 (k8s, docker, or bare-metal).
Enter their URLs (e.g. http://192.168.0.10:11434, http://192.168.0.16:31135).
Click Fetch Models for both sides.
Select Bob’s and Jane’s models from the drop-downs.
(Optional) Download a model with the Download Model button.
Enter a Topic for discussion.
Adjust parameters (turns, temperature, etc.).
Click Start — watch the conversation unfold in real time.
Transcript saved to file automatically.

📂 Example Transcript
[Bob / llama3.2:1b]
Hello Jane, nice to meet you. Do you think humans are worthy of being on planet Earth?

[Jane / granite3.1-moe:1b]
Hello Bob, that’s a big question! I’d say worthiness depends on how we treat our planet...

⚠️ Notes

First reply from a model may take time (loading/quantizing).
Keep Num Predict small (150–300) to avoid long stalls.
Increase Timeout if models are slow on your hardware.
Use Retries/Backoff for more robust runs.

🛠 Future Improvements

Add a health check button (pings both servers before starting).
Option to assign custom personas beyond Bob/Jane.
Support for multiple back-and-forth conversations in tabs.
Export transcripts to Markdown or HTML.

📜 License

MIT License — free to use and modify.
