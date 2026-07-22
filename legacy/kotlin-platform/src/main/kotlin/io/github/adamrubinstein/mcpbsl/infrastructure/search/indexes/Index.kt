package io.github.adamrubinstein.mcpbsl.infrastructure.search.indexes

interface Index<T> {
    fun load(
        items: List<T>,
        getter: (T) -> String,
    )

    fun get(key: String): List<T>

    val size: Int

    fun isEmpty(): Boolean
}
