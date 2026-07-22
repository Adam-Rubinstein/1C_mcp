package io.github.adamrubinstein.mcpbsl.infrastructure.search

import io.github.adamrubinstein.mcpbsl.business.entities.Definition
import io.github.adamrubinstein.mcpbsl.business.valueobjects.ApiType
import io.github.adamrubinstein.mcpbsl.infrastructure.persistent.storage.PlatformContextStorage
import io.github.adamrubinstein.mcpbsl.infrastructure.search.SimpleSearchEngine.SearchResult

class WordOrderSearch(
    private val context: PlatformContextStorage,
) {
    fun search(
        query: String,
        type: ApiType?,
    ): List<SearchResult> {
        val words = query.split("\\s+".toRegex()).filter { it.isNotBlank() }
        val results = mutableListOf<SearchResult>()

        val searchTargets =
            when (type) {
                ApiType.METHOD -> context.methods.asSequence()
                ApiType.PROPERTY -> context.properties.asSequence()
                ApiType.TYPE -> context.types.asSequence()
                else ->
                    sequenceOf(
                        context.methods,
                        context.properties,
                        context.types,
                    ).flatten()
            }

        searchTargets.forEach { item ->
            val itemName = item.name
            val matchingWords = countMatchingWords(itemName, words)

            if (matchingWords > 0) {
                results.add(wordOrder(item, matchingWords))
            }
        }

        return results
    }

    private fun countMatchingWords(
        elementName: String,
        queryWords: List<String>,
    ): Int {
        val nameLower = elementName.lowercase()
        return queryWords.count { word ->
            nameLower.contains(word.lowercase())
        }
    }
}

fun wordOrder(
    item: Definition,
    wordsMatched: Int,
) = SearchResult(item, 4, wordsMatched, "word-order")
