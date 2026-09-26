"""Load the Ollama guard for every interpreter that has tests/ on PYTHONPATH.

``python -m unittest discover`` does not import the tests package, and child
processes do not see ``unittest.mock`` patches. Putting ``tests`` on
PYTHONPATH makes this file run at startup in the parent and in those children.
"""

import ollama_guard

ollama_guard.install()
