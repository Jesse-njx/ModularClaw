Dear AI Agent, here are some baselines you should obey when working on this project:

 - Try to not edit core files, such as core.py, session.py, and config_loader.py. This includes creating new core files. If you're planning on doing so, give me a short pitch on WHY it is necessary and structurally beneficial to edit the core.
 - If you can, you're supposed to create a module. Look at existing modules for reference. You have access to the entire codebase, so you should understand how modules work. 

# Consistancy measures:
 - Keep ALL prompts within the config folder, in the corresponding json file of the module (e.g. `config/sender.json`, `config/memory.json`, `config/file_system.json`).
 - Keep module naming strict and predictable: file name, class name, and registration name should all match.
 - For every new module, create or update its matching config file under `config/<module>.json`.
 - Keep version values aligned between module code (`VERSION`) and its config `version`.
 - Reuse the existing session/status patterns (`Ready to send`, claim/release flow) instead of inventing module-specific variants.
 - Follow existing coding style in nearby files and keep changes focused, minimal, and easy to review.
 - Always check if a part of a text is claimed before executing anything.