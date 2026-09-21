// Test-only executable selection; production never runs this helper.
// The bridge scripts import thth from the repo, never pytest, so the platform
// python3 on PATH is enough. Set PYTHON_FOR_TESTS to point at another one
// (pyproject asks for >=3.10; the bridge itself stays 3.9-compatible).
export const pythonForTests=(env=process.env)=>env.PYTHON_FOR_TESTS||'python3';
