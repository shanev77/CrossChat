AI Cross-Chat GUI (Bob ↔ Jane)

I started originally with a command line version but grew into a desktop GUI application that lets you run two Ollama servers as conversational agents (e.g., Bob on AIHub and Jane on NODE01) and watch them debate, discuss, or collaborate on any topic you choose.
Instead of juggling terminal commands, this tool provides a clean interface with buttons, text fields, and live conversation output.

This project was born from my home Kubernetes cluster, where I run multiple Ollama instances across different nodes.

AIHub (x86 node in the cluster) runs one Ollama instance.
NODE01 (Raspberry Pi 5 node) runs another Ollama instance.

The AI Cross-Chat GUI lets me connect to both of these Ollama endpoints and have them converse with each other automatically. Instead of typing curl requests or juggling terminal sessions, this app provides a friendly GUI where you can:
Fetch models from each node.
Choose which model Bob (AIHub) and Jane (NODE01) should use.
Define a topic and let them discuss it turn by turn.
Save the whole conversation as a transcript for later.
This makes it easy to explore how different models respond to the same topics, compare performance across hardware, and just enjoy watching two LLMs debate through my Kubernetes cluster.

- Features

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

- Getting Started

Prerequisites

Python 3.9+

pip install requests


- Usage

Start your Ollama servers on AIHub and NODE01 or whereever you are running the 2 ollama server (k8s, docker, or bare-metal).
Enter their URLs (e.g. http://192.168.0.10:11434, http://192.168.0.16:31135).
Click Fetch Models for both sides.
Select Bob’s and Jane’s models from the drop-downs.
(Optional) Download a model with the Download Model button.
Enter a Topic for discussion.
Adjust parameters (turns, temperature, etc.).
Click Start — watch the conversation unfold in real time.
Transcript saved to file automatically.

- Example Transcript
[Bob / llama3.2:1b]
Hello Jane, nice to meet you. Do you think humans are worthy of being on planet Earth?

[Jane / granite3.1-moe:1b]
Hello Bob, that’s a big question! I’d say worthiness depends on how we treat our planet...

- Notes

First reply from a model may take time (loading/quantizing).
Keep Num Predict small (150–300) to avoid long stalls.
Increase Timeout if models are slow on your hardware.
Use Retries/Backoff for more robust runs.

- Future Improvements

Add a health check button (pings both servers before starting).
Option to assign custom personas beyond Bob/Jane.
Support for multiple back-and-forth conversations in tabs.
Export transcripts to Markdown or HTML.

- License

MIT License — free to use and modify.
