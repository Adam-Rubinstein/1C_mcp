package io.github.adamrubinstein.mcpbsl.business.services

import io.github.adamrubinstein.mcpbsl.business.entities.Definition
import io.github.adamrubinstein.mcpbsl.business.entities.Signature

interface ResponseFormatterService {
    fun formatError(e: Throwable): String

    fun formatQuery(query: String): String

    fun formatSearchResults(result: List<Definition>): String

    fun formatMember(definition: Definition?): String

    fun formatTypeMembers(definitions: List<Definition>): String

    fun formatConstructors(
        result: List<Signature>,
        typeName: String,
    ): String
}
