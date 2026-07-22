package io.github.adamrubinstein.mcpbsl.install

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.CsvSource

class GitHubInstallUrlParserTest {
    @ParameterizedTest
    @CsvSource(
        "https://github.com/Adam-Rubinstein/1C_mcp_bsl,Adam-Rubinstein,1C_mcp_bsl",
        "https://github.com/Adam-Rubinstein/1C_mcp_bsl.git,Adam-Rubinstein,1C_mcp_bsl",
        "http://github.com/O/R,O,R",
        "Adam-Rubinstein/1C_mcp_bsl,Adam-Rubinstein,1C_mcp_bsl",
        "  org/repo  ,org,repo",
    )
    fun parse(
        input: String,
        owner: String,
        repo: String,
    ) {
        val spec = GitHubInstallUrlParser.parse(input)
        assertThat(spec).isNotNull
        assertThat(spec!!.owner).isEqualTo(owner)
        assertThat(spec.repo).isEqualTo(repo)
        assertThat(spec.releasesLatestApiPath()).isEqualTo("repos/$owner/$repo/releases/latest")
    }

    @Test
    fun `parse rejects garbage`() {
        assertThat(GitHubInstallUrlParser.parse("not-a-repo")).isNull()
        assertThat(GitHubInstallUrlParser.parse("https://gitlab.com/a/b")).isNull()
    }
}
