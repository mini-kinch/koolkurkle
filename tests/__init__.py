# Test package for Mailroom daily RAG (no network).
# Do not import ollama_guard here. discover -s tests loads it as a top-level
# module; a relative import would install a second copy and a second exception
# type. sitecustomize.py (PYTHONPATH=tests) and the test modules call install().
