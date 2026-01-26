import sys
from typing import List, Dict, Any, Callable
from logger import get_logger

logger = get_logger()


class MemoryLimitedIndexer:
    """
    Manages document indexing with memory limits.
    Automatically flushes batches when memory threshold is reached.
    """
    
    def __init__(self, max_memory_bytes: int, batch_callback: Callable[[List[str], List[Dict], List[str]], None]):
        """
        Args:
            max_memory_bytes: Maximum memory to use for buffering documents
            batch_callback: Function to call when flushing batch (documents, metadatas, ids)
        """
        self.max_memory_bytes = max_memory_bytes
        self.batch_callback = batch_callback
        self.documents: List[str] = []
        self.metadatas: List[Dict] = []
        self.ids: List[str] = []
        self.current_memory = 0
        self.total_chunks = 0
        self.total_batches = 0
    
    def _estimate_size(self, obj: Any) -> int:
        """
        Estimates memory size of an object in bytes.
        Uses sys.getsizeof with overhead for containers.
        """
        size = sys.getsizeof(obj)
        
        if isinstance(obj, dict):
            size += sum(sys.getsizeof(k) + sys.getsizeof(v) for k, v in obj.items())
        elif isinstance(obj, (list, tuple)):
            size += sum(sys.getsizeof(item) for item in obj)
        
        return size
    
    def add_chunk(self, document: str, metadata: Dict[str, Any], doc_id: str) -> None:
        """
        Adds a chunk to the buffer. Flushes if memory limit exceeded.
        
        Args:
            document: Document text
            metadata: Document metadata
            doc_id: Unique document ID
        """
        chunk_size = (
            self._estimate_size(document) +
            self._estimate_size(metadata) +
            self._estimate_size(doc_id)
        )
        
        if self.current_memory + chunk_size > self.max_memory_bytes and self.documents:
            self.flush()
        
        self.documents.append(document)
        self.metadatas.append(metadata)
        self.ids.append(doc_id)
        self.current_memory += chunk_size
        self.total_chunks += 1
    
    def flush(self) -> None:
        """
        Flushes current batch to the callback.
        Clears buffers and resets memory counter.
        """
        if not self.documents:
            return
        
        logger.debug(
            f"Flushing batch {self.total_batches + 1}: "
            f"{len(self.documents)} chunks, "
            f"{self.current_memory / 1024 / 1024:.2f} MB"
        )
        
        try:
            self.batch_callback(self.documents, self.metadatas, self.ids)
            self.total_batches += 1
        except Exception as e:
            logger.error(f"Error flushing batch: {e}", exc_info=True)
            raise
        finally:
            self.documents.clear()
            self.metadatas.clear()
            self.ids.clear()
            self.current_memory = 0
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Returns indexing statistics.
        
        Returns:
            Dictionary with total_chunks, total_batches, current_buffer_size
        """
        return {
            "total_chunks": self.total_chunks,
            "total_batches": self.total_batches,
            "current_buffer_chunks": len(self.documents),
            "current_buffer_bytes": self.current_memory,
            "max_memory_bytes": self.max_memory_bytes,
        }
