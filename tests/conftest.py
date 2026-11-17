import os
import tempfile

# Keep the real evaluation store out of the working tree during tests. This must
# be set before rag_service.config is imported by any test module.
os.environ.setdefault("RAG_EVAL_STORE_DIR", tempfile.mkdtemp(prefix="rag-eval-store-"))
