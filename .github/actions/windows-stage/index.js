// One stage of the Windows build: continues the build tree of the previous
// stage (or of the run given as from_run), runs windows/build.py for up to 3.5
// hours of compiling and then either saves the build tree for the next stage
// (artifact cerium-windows-x64-build-<job>) or, once the build is finished,
// uploads the installer and the .zip (artifact cerium-windows-x64).
// From ungoogled-chromium-windows's stage action.
import * as core from '@actions/core';
import * as io from '@actions/io';
import * as exec from '@actions/exec';
import { DefaultArtifactClient } from '@actions/artifact';
import * as glob from '@actions/glob';

const BUILD_TIMEOUT_EXIT_CODE = 124;
const HARNESS = 'C:\\cerium\\windows';
const BUILD = `${HARNESS}\\build`;
const TREE_ARTIFACT_PREFIX = 'cerium-windows-x64-build';
const PACKAGES_ARTIFACT = 'cerium-windows-x64';

async function upload(artifact, name, files, rootDirectory, retentionDays) {
    for (let i = 0; i < 5; ++i) {
        try {
            // An earlier attempt of this job may have uploaded it already.
            await artifact.deleteArtifact(name);
        } catch (e) {
            // ignored: there is none
        }
        try {
            await artifact.uploadArtifact(name, files, rootDirectory,
                {retentionDays, compressionLevel: 0});
            return;
        } catch (e) {
            console.error(`Upload of ${name} failed: ${e}`);
            // Wait 10 seconds between the attempts
            await new Promise(r => setTimeout(r, 10000));
        }
    }
    throw new Error(`Could not upload ${name}`);
}

async function run() {
    process.on('SIGINT', function() {
    });
    const finished = core.getBooleanInput('finished', {required: true});
    const fromArtifact = core.getBooleanInput('from_artifact', {required: true});
    const fromRun = core.getInput('from_run');
    const jobs = core.getInput('jobs') || '3';
    console.log(`finished: ${finished}, artifact: ${fromArtifact}, run: ${fromRun || 'this one'}, jobs: ${jobs}`);
    if (finished) {
        core.setOutput('finished', true);
        return;
    }

    const artifact = new DefaultArtifactClient();

    if (fromArtifact) {
        const args = [`${import.meta.dirname}\\download.js`, BUILD];
        if (fromRun)
            args.push(fromRun);
        await exec.exec(process.execPath, args, {
            env: {...process.env, CERIUM_ARTIFACT_TOKEN: core.getInput('token')}
        });
        await exec.exec('7z', ['x', `${BUILD}\\artifacts.zip`, `-o${BUILD}`, '-y']);
        await io.rmRF(`${BUILD}\\artifacts.zip`);
    }

    // httplib2 and clang: as upstream; cryptography: to sign the bundled
    // extensions (utils/crx3.py).
    await exec.exec('python', ['-m', 'pip', 'install', 'httplib2==0.22.0', 'clang==21.1.7',
        'cryptography'], {cwd: HARNESS});
    const retCode = await exec.exec('python', ['build.py', '--ci', '-j', jobs], {
        cwd: HARNESS,
        ignoreReturnCode: true
    });
    if (retCode === 0) {
        core.setOutput('finished', true);
        const globber = await glob.create(`${BUILD}\\cerium-*`, {matchDirectories: false});
        const packageList = await globber.glob();
        if (packageList.length === 0)
            throw new Error(`build.py made no packages in ${BUILD}`);
        console.log(`Packages: ${packageList.join(', ')}`);
        await upload(artifact, PACKAGES_ARTIFACT, packageList, BUILD, 30);
    } else if (retCode === BUILD_TIMEOUT_EXIT_CODE) {
        await new Promise(r => setTimeout(r, 5000));
        // 7z exits with 1 for warnings (e.g. a file it could not open) and
        // with 2 or more for errors (e.g. a full disk).
        const zipCode = await exec.exec('7z', ['a', '-tzip', `${HARNESS}\\artifacts.zip`,
            `${BUILD}\\src`, '-mx=3', '-mtc=on'], {ignoreReturnCode: true});
        if (zipCode > 1)
            throw new Error(`Could not archive the build tree (7z exit code ${zipCode})`);
        // Each stage saves its tree under its own name (e.g. -build-3) and
        // removes the older ones only then: if the upload fails, the next try
        // (re-running the failed job) can still continue the previous tree.
        const job = (process.env.GITHUB_JOB || 'stage').replace(/^build-/, '');
        const name = `${TREE_ARTIFACT_PREFIX}-${job}`;
        await upload(artifact, name, [`${HARNESS}\\artifacts.zip`], HARNESS, 7);
        const {artifacts} = await artifact.listArtifacts();
        for (const old of artifacts) {
            if (old.name.startsWith(TREE_ARTIFACT_PREFIX) && old.name !== name) {
                try {
                    await artifact.deleteArtifact(old.name);
                } catch (e) {
                    console.error(`Could not delete ${old.name}: ${e}`);
                }
            }
        }
        core.setOutput('finished', false);
    } else {
        throw new Error(`Build failed with exit code ${retCode}`);
    }
}

run().catch(err => core.setFailed(err.message));
