// Test-only executable selection; production never runs this helper.
export const pythonForTests=(env=process.env)=>env.PYTHON_FOR_TESTS||'python3';
