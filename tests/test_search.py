import os
import sys

sys.path.append(os.getcwd())

from mcp_server import get_index_stats, search_codebase

print("Testing search functionality...")

stats = get_index_stats()
print(f"Index stats: {stats}")

results = search_codebase("startup check")
print(f"\nSearch Results:\n{results}")
