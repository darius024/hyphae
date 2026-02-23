// Simulate the top-level execution of app.js to find the first runtime crash
// by checking for patterns that would throw at top-level

const fs = require('fs');
const code = fs.readFileSync('/Users/stefi/Desktop/Projects/Hyphae/hyphae/web/static/app.js', 'utf8');
const lines = code.split('\n');

// Find all top-level statements (not indented) that call methods on variables
// These could crash if the variable is undefined (not null — getElementById returns null not undefined)
// null.addEventListener throws TypeError
// We need to find: VARNAME.addEventListener or VARNAME.classList etc at column 0

const topLevelMethodCall = /^([a-zA-Z_$][a-zA-Z0-9_$]*)\.([a-zA-Z]+)\s*\(/;
const varDecls = new Map(); // varName -> line

// First pass: collect all const/let/var declarations
lines.forEach((line, i) => {
    const m = line.match(/^(?:const|let|var)\s+(\w+)\s*=/);
    if (m) varDecls.set(m[1], i + 1);
});

// Second pass: find top-level method calls where var is declared AFTER current line
lines.forEach((line, i) => {
    const m = line.match(topLevelMethodCall);
    if (!m) return;
    const varName = m[1];
    if (['document', 'window', 'console', 'Math', 'JSON', 'Object', 'Array', 'Promise', 'setTimeout', 'clearTimeout'].includes(varName)) return;
    const declLine = varDecls.get(varName);
    if (declLine === undefined) {
        console.log(`Line ${i+1}: '${varName}' used but NEVER declared → will crash if undefined`);
    } else if (declLine > i + 1) {
        console.log(`Line ${i+1}: '${varName}' declared at line ${declLine} but used before declaration → TDZ crash`);
    }
});

console.log('\nDone.');
