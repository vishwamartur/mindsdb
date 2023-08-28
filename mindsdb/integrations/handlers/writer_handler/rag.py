from collections import defaultdict
from typing import List

from langchain.llms import Writer

from mindsdb.integrations.handlers.writer_handler.settings import (
    PersistedVectorStoreIndexConfig,
    PersistedVectorStoreLoader,
    PersistedVectorStoreLoaderConfig,
    VectorStoreIndexLoader,
    WriterHandlerParameters,
    load_embeddings_model,
)
from mindsdb.utilities.log import get_log

logger = get_log(logger_name=__name__)


class QuestionAnswerer:
    def __init__(self, args: WriterHandlerParameters):

        self.output_data = defaultdict(list)

        self.args = args

        self.embeddings_model = load_embeddings_model(args.embeddings_model_name)

        self.persist_directory = args.vector_store_storage_path

        self.collection_name = args.collection_name

        vector_store_config = PersistedVectorStoreLoaderConfig(
            vector_store_name=args.vector_store_name,
            embeddings_model=self.embeddings_model,
            persist_directory=self.persist_directory,
            collection_name=self.collection_name,
        )

        self.vector_store_loader = PersistedVectorStoreLoader(vector_store_config)

        self.persisted_vector_store = self.vector_store_loader.load_vector_store()

        if args.external_index_name:

            # todo fix this - for llamaindex not sure how to decouple retrieval from generation

            vector_store_index_config = PersistedVectorStoreIndexConfig(
                vector_store_name=args.vector_store_name,
                vector_store=self.persisted_vector_store,
                embeddings_model=self.embeddings_model,
                persist_directory=self.persist_directory,
                collection_name=args.collection_name,
                index_name=args.external_index_name,
            )
            vector_store_index_loader = VectorStoreIndexLoader(
                vector_store_index_config
            )

            self.index = vector_store_index_loader.load_vector_store_index()

        self.prompt_template = args.prompt_template

        self.llm = Writer(**args.llm_params.dict())

    def __call__(self, question: str):
        return self.query(question)

    def _prepare_prompt(self, vector_store_response, question):

        context = [doc.page_content for doc in vector_store_response]

        combined_context = "\n\n".join(context)

        if self.args.summarize_context:
            return self.summarize_context(combined_context, question)

        return self.prompt_template.format(question=question, context=combined_context)

    def summarize_context(self, combined_context: str, question: str):

        summarization_prompt_template = self.args.summarization_prompt_template

        summarization_prompt = summarization_prompt_template.format(
            context=combined_context, question=question
        )

        summarized_context = self.llm(prompt=summarization_prompt)

        return self.prompt_template.format(
            question=question, context=summarized_context
        )

    def _query_index(self, question: str):

        return self.index.query(
            question,
        )

    def query_vector_store(self, question: str) -> List:

        return self.persisted_vector_store.similarity_search(
            query=question,
            k=self.args.top_k,
        )

    def query(self, question: str):
        logger.debug(f"Querying: {question}")

        if not self.args.use_external_index:

            vector_store_response = self.query_vector_store(question)

            formatted_prompt = self._prepare_prompt(vector_store_response, question)

        else:
            vector_index_response = self._query_index(question)
            # todo make parser for llamaindexresponse
            # formatted_prompt = self._prepare_prompt(vector_index_response, question)
            raise NotImplementedError("llamaindexresponse parser not implemented")

        llm_response = self.llm(prompt=formatted_prompt)

        result = defaultdict(list)

        result["question"].append(question)
        result["answer"].append(llm_response)

        sources = defaultdict(list)

        for idx, document in enumerate(vector_store_response):
            sources["sources_document"].append(document.metadata["source"])
            sources["column"].append(document.metadata.get("column"))
            sources["sources_row"].append(document.metadata.get("row"))
            sources["sources_content"].append(document.page_content)

        result["source_documents"].append(dict(sources))

        return result