// Downloads the newest build tree (artifact cerium-windows-x64-build-*) of this
// workflow run, or of the run given as second argument, into a directory:
//
//   node download.js <directory> [run ID]
//
// The token for another run's artifacts comes from CERIUM_ARTIFACT_TOKEN.
// Runs in a separate process so file handles leaked by failed download retries
// in @actions/artifact are released before the downloaded archive is
// extracted (from ungoogled-chromium-windows's stage action).
import { DefaultArtifactClient } from '@actions/artifact';

const TREE_ARTIFACT_PREFIX = 'cerium-windows-x64-build';

async function run() {
    const [path, runId] = process.argv.slice(2);
    const artifact = new DefaultArtifactClient();
    let options = {};
    if (runId) {
        const [repositoryOwner, repositoryName] = process.env.GITHUB_REPOSITORY.split('/');
        options = {
            findBy: {
                token: process.env.CERIUM_ARTIFACT_TOKEN,
                workflowRunId: Number(runId),
                repositoryOwner,
                repositoryName,
            },
        };
    }
    const {artifacts} = await artifact.listArtifacts(options);
    // Artifact IDs grow over time: the highest is the newest tree.
    const trees = artifacts.filter(a => a.name.startsWith(TREE_ARTIFACT_PREFIX))
        .sort((a, b) => b.id - a.id);
    if (trees.length === 0) {
        throw new Error(`No build tree (${TREE_ARTIFACT_PREFIX}-*) in ${runId ? `run ${runId}` : 'this run'}`);
    }
    const tree = trees[0];
    console.log(`Downloading ${tree.name} (${(tree.size / 2 ** 30).toFixed(1)} GiB)` +
        (runId ? ` from run ${runId}` : ''));
    await artifact.downloadArtifact(tree.id, {...options, path});
}

run().catch(err => {
    console.error(err);
    process.exit(1);
});
