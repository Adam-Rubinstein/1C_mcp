package io.github.adamrubinstein.mcpbsl.infrastructure.search.indexes

import java.util.SortedMap

class StartWithIndex<T> : Index<T> {
    private lateinit var sortedValues: SortedMap<String, T>

    override fun load(
        items: List<T>,
        getter: (T) -> String,
    ) {
        sortedValues = items.associateBy { getter(it).lowercase() }.toSortedMap()
    }

    override fun get(key: String): List<T> {
        val searchKey = key.lowercase()
        val nextKey = searchKey + Char.MAX_VALUE
        return sortedValues
            .subMap(searchKey, nextKey)
            .map { it.value }
    }

    override val size: Int
        get() = sortedValues.size

    override fun isEmpty(): Boolean = sortedValues.isEmpty()
}
