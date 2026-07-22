package io.github.adamrubinstein.mcpbsl.infrastructure.search

import io.github.adamrubinstein.mcpbsl.business.entities.Definition
import io.github.adamrubinstein.mcpbsl.business.valueobjects.ApiType
import io.github.adamrubinstein.mcpbsl.infrastructure.search.SimpleSearchEngine.SearchResult
import io.github.adamrubinstein.mcpbsl.infrastructure.search.indexes.Indexes

class RegularSearch(
    private val indexes: Indexes,
) {
    fun search(
        query: String,
        type: ApiType?,
    ): List<SearchResult> {
        val results = mutableListOf<SearchResult>()

        var inMethods = type == ApiType.METHOD
        var inProperty = type == ApiType.PROPERTY
        var inType = type == ApiType.TYPE

        if (!(inMethods || inProperty || inType)) {
            inMethods = true
            inProperty = true
            inType = true
        }
        if (inMethods) {
            indexes.methods
                .get(query)
                .forEach { results.add(regular(it)) }
        }
        if (inProperty) {
            indexes.properties
                .get(query)
                .forEach { results.add(regular(it)) }
        }
        if (inType) {
            indexes.types
                .get(query)
                .forEach { results.add(regular(it)) }
        }

        return results
    }
}

fun regular(item: Definition) = SearchResult(item, 3, 0, "regular")
