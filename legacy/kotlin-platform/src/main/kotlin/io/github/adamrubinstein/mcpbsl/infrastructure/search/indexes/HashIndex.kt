package io.github.adamrubinstein.mcpbsl.infrastructure.search.indexes

import java.util.concurrent.ConcurrentHashMap

class HashIndex<T> : Index<T> {
    private val index = ConcurrentHashMap<String, T>()

    override val size: Int
        get() = index.size

    override fun isEmpty() = index.isEmpty()

    override fun load(
        items: List<T>,
        getter: (T) -> String,
    ) {
        items.associateByTo(index) {
            getter(it).lowercase()
        }
    }

    override fun get(key: String): List<T> {
        val value = index[key.lowercase()]
        return if (value != null) listOf(value) else emptyList()
    }
}
