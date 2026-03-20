# Executor module (simple guide)

This page explains **`modules/executor.py`** in plain language: what it does, and **why the Sender waits** until it is finished.

You do **not** need to understand the whole framework to follow this.

---

## What is the Executor for?

Think of the AI as someone who can write a **small note in a special format**. That note says: “please run this shell command.”

The **Executor** is the part of the program that:

1. Finds those notes in the conversation.
2. Runs the command on the computer.
3. Writes the result back into the conversation so the AI can read it later.

If the Executor is busy or still waiting for a command to finish, **we should not call the AI again yet**. That is the main way the Executor “gets in the way” of the Sender—in a good way.

---

## What does one of those notes look like?

The Executor only cares about context items that are:

- normal text from the AI’s reply, **and**
- marked as JSON (`label` is `json`), **and**
- after parsing, the JSON is a **dictionary** with `type` = `"tool_call"` and `name` = `"execute_command"`.

A simplified example:

```json
{
  "type": "tool_call",
  "name": "execute_command",
  "arguments": { "command": "ls" }
}
```

So the AI’s answer is not only chat—it can contain this kind of JSON. The Executor looks for it on every tick.

---

## Step by step: what happens?

1. **Spot the tool call**  
   The Executor scans the session’s context list. When it finds JSON like above and nobody else has “locked” that spot yet, it takes it.

2. **Claim the spot**  
   It marks that index as **claimed** so nothing else tries to run the same command twice.

3. **Run the command in the background**  
   The command runs in a **separate thread** so the program does not freeze. While it runs, the Executor treats work as **not done**.

4. **Put the answer back**  
   When the command finishes, the Executor **replaces** that JSON blob with a **tool result** (a different type in context). It also clears the claim and sets a flag that says: **we need another loop** (so the system can keep going).

5. **Tell Sender “wait” or “ok”**  
   The Executor sets its own status for `"Ready to send"` to either **`pending`** (something still to do) or **`ready`** (nothing left to run).  
   The **Sender** waits until **every** other module says **`ready`** for that same status key.

So: **while a command is running or a tool call is still waiting, the Executor says “pending,” and the Sender does not send.**

---

## How this connects to the Sender (short version)

- The **Sender** asks: “Is everyone happy to send?”  
- The **Executor** answers **“pending”** if there is still a command to run or one is running.  
- When everything is clear, the Executor answers **“ready”**.  
- Only then can the Sender go ahead (together with the other modules doing the same check).

You can read more detail in [`sender.md`](sender.md).

---

## Extra: the tool prompt

When a session starts, the Executor can add a piece of text from config (`prompt` in the executor config) into context. That text is meant to teach the model **how** to format tool calls. You can think of it as “instructions pasted into the chat at the start.”

---

## Safety note

The Executor runs **`shell=True`** with the string from the model. That is powerful and risky in real deployments. This doc only explains **how** it interacts with Sender; securing it is a separate topic.
