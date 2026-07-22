package io.github.adamrubinstein.mcpbsl.install

/**
 * Разбор URL/спеки репозитория GitHub — тот же контракт, что в
 * [scripts/install-mcp.ps1] и [scripts/install-mcp.sh] (parse_github / Parse-GitHubSpec).
 */
data class GitHubRepoSpec(
    val owner: String,
    val repo: String,
) {
    fun releasesLatestApiPath(): String = "repos/$owner/$repo/releases/latest"
}

object GitHubInstallUrlParser {
    private val fullUrl =
        Regex(
            """^https?://github\.com/([^/]+)/([^/#?]+)""",
            RegexOption.IGNORE_CASE,
        )
    private val shortForm = Regex("""^([^/]+)/([^/]+)$""")

    fun parse(input: String): GitHubRepoSpec? {
        val t = input.trim()
        fullUrl.find(t)?.let {
            val owner = it.groupValues[1]
            val repo = it.groupValues[2].removeSuffix(".git")
            return GitHubRepoSpec(owner, repo)
        }
        shortForm.find(t)?.let {
            return GitHubRepoSpec(it.groupValues[1].trim(), it.groupValues[2].removeSuffix(".git").trim())
        }
        return null
    }
}
