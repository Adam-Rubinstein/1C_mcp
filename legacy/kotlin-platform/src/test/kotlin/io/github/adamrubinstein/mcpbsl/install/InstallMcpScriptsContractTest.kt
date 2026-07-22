package io.github.adamrubinstein.mcpbsl.install

import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Assumptions.assumeTrue
import org.junit.jupiter.api.Test
import java.io.File

/**
 * Документирует обязательные фрагменты в скриптах установки (регресс при правках).
 */
class InstallMcpScriptsContractTest {
    private val repoRoot = File(System.getProperty("user.dir"))

    @Test
    fun `install-mcp ps1 contains documented knobs`() {
        val ps1 = File(repoRoot, "scripts/install-mcp.ps1").readText(Charsets.UTF_8)
        assertThat(ps1).contains("OverrideJarPath")
        assertThat(ps1).contains("OverridePlatformPath")
        assertThat(ps1).contains("function Parse-GitHubSpec")
        assertThat(ps1).contains("function Get-ReleaseJarUrl")
        assertThat(ps1).contains("[DryRun] JAR не скачивается")
    }

    @Test
    fun `install-mcp sh contains documented env overrides`() {
        val sh = File(repoRoot, "scripts/install-mcp.sh").readText(Charsets.UTF_8)
        assertThat(sh).contains("INSTALL_MCP_JAR_PATH")
        assertThat(sh).contains("INSTALL_MCP_PLATFORM_PATH")
        assertThat(sh).contains("INSTALL_MCP_PYTHON")
        assertThat(sh).contains("WindowsApps")
        assertThat(sh).contains("parse_github")
    }

    @Test
    fun `smoke script exists`() {
        val smoke = File(repoRoot, "scripts/test/smoke-install-mcp.sh")
        assumeTrue(smoke.isFile, "smoke script present")
        val text = smoke.readText(Charsets.UTF_8)
        assertThat(text).contains("INSTALL_MCP_SOURCE_ROOT")
        assertThat(text).contains("INSTALL_MCP_PLATFORM_PATH")
    }
}
