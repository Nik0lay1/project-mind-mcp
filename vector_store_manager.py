import hashlib
import json

from cache_manager import TTLCache
from config import MODEL_NAME, VECTOR_STORE_DIR
from logger import get_logger

logger = get_logger()


class VectorStoreManager:
    """
    Manages ChromaDB vector store operations.
    Encapsulates client, collection, and embedding function management.
    """

    def __init__(self, collection_name: str = "project_codebase"):
        """
        Initialize vector store manager.

        Args:
            collection_name: Name of the ChromaDB collection
        """
        self.collection_name = collection_name
        self.chroma_client = None
        self.collection = None
        self.embedding_fn = None
        self._initialized = False
        self._query_cache = TTLCache(ttl_seconds=300, max_size=100)

    def initialize(self) -> bool:
        """
        Initializes ChromaDB client, embedding function, and collection.

        Returns:
            True if initialization successful, False otherwise
        """
        if self._initialized and self.collection is not None:
            return True

        logger.info("Initializing Vector Store...")

        try:
            import chromadb
            from chromadb.utils import embedding_functions
            from sentence_transformers import SentenceTransformer

            class LocalSentenceTransformerEmbeddingFunction(embedding_functions.EmbeddingFunction):
                def __init__(self, model_name: str) -> None:
                    self.model = SentenceTransformer(model_name)

                def __call__(self, input: list[str]) -> list[list[float]]:
                    return self.model.encode(input).tolist()

            self.chroma_client = chromadb.PersistentClient(path=str(VECTOR_STORE_DIR))
            self.embedding_fn = LocalSentenceTransformerEmbeddingFunction(MODEL_NAME)
            self.collection = self.chroma_client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=self.embedding_fn
            )

            self._initialized = True
            logger.info("Vector Store initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize ChromaDB: {e}", exc_info=True)
            return False

    def get_collection(self):
        """
        Gets the collection, initializing if needed.

        Returns:
            ChromaDB collection or None if initialization failed
        """
        if not self._initialized:
            if not self.initialize():
                return None
        return self.collection

    def clear_collection(self) -> str | None:
        """
        Clears the current collection by deleting and recreating it.

        Returns:
            Error message if failed, None if successful
        """
        if not self.chroma_client:
            return "ChromaDB client not initialized"

        try:
            self.chroma_client.delete_collection(self.collection_name)
            self.collection = self.chroma_client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=self.embedding_fn
            )
            logger.info(f"Collection '{self.collection_name}' cleared successfully")
            return None
        except Exception as e:
            error_msg = f"Error clearing collection: {e}"
            logger.error(error_msg)
            return error_msg

    def get_count(self) -> int | None:
        """
        Gets the number of items in the collection.

        Returns:
            Number of items, or None if collection not initialized
        """
        coll = self.get_collection()
        if coll is None:
            return None

        try:
            return coll.count()
        except Exception as e:
            logger.error(f"Error getting collection count: {e}")
            return None

    def query(
        self,
        query_texts: list[str],
        n_results: int = 5,
        where: dict | None = None,
        where_document: dict | None = None
    ) -> dict | None:
        """
        Queries the vector store with caching.

        Args:
            query_texts: List of query strings
            n_results: Number of results to return
            where: Optional metadata filter
            where_document: Optional document content filter

        Returns:
            Query results or None if query failed
        """
        cache_key = self._generate_cache_key(query_texts, n_results, where, where_document)
        cached_result = self._query_cache.get(cache_key)
        if cached_result is not None:
            logger.debug(f"Cache hit for query: {query_texts[0][:50]}...")
            return cached_result

        coll = self.get_collection()
        if coll is None:
            return None

        try:
            result = coll.query(
                query_texts=query_texts,
                n_results=n_results,
                where=where,
                where_document=where_document
            )
            self._query_cache.put(cache_key, result)
            return result
        except Exception as e:
            logger.error(f"Error querying collection: {e}", exc_info=True)
            return None

    def _generate_cache_key(
        self,
        query_texts: list[str],
        n_results: int,
        where: dict | None,
        where_document: dict | None
    ) -> str:
        """
        Generates a unique cache key for query parameters.

        Args:
            query_texts: List of query strings
            n_results: Number of results
            where: Metadata filter
            where_document: Document filter

        Returns:
            Hash string for cache key
        """
        key_data = {
            "query_texts": query_texts,
            "n_results": n_results,
            "where": where,
            "where_document": where_document
        }
        key_str = json.dumps(key_data, sort_keys=True)
        return hashlib.sha256(key_str.encode()).hexdigest()

    def get_query_cache_stats(self):
        """
        Returns query cache statistics.

        Returns:
            Dictionary with cache statistics
        """
        return self._query_cache.get_stats()

    def upsert(
        self,
        documents: list[str],
        metadatas: list[dict],
        ids: list[str]
    ) -> bool:
        """
        Upserts documents into the collection.

        Args:
            documents: List of document texts
            metadatas: List of metadata dicts
            ids: List of document IDs

        Returns:
            True if successful, False otherwise
        """
        coll = self.get_collection()
        if coll is None:
            return False

        try:
            coll.upsert(
                documents=documents,
                metadatas=metadatas,
                ids=ids
            )
            return True
        except Exception as e:
            logger.error(f"Error upserting to collection: {e}", exc_info=True)
            return False
