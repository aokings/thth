// Test-only executable selection; production never runs this helper.
export const pythonForTests=(env=process.env,platform=process.platform)=>env.PYTHON_FOR_TESTS||(platform==='darwin'?'/opt/homebrew/Caskroom/miniforge/base/bin/python':'python3');
