echo "🧹 Cleaning Python caches and local state..."
rm -rf .pytest_cache .ruff_cache .mypy_cache
find . -type d -name "__pycache__" -exec rm -rf {} +
echo "✅ Clean up complete."