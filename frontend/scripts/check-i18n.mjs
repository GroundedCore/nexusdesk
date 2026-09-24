import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';
const languages=['zh-CN','zh-TW','en','hi'];
const dictionaries=languages.map(lang=>JSON.parse(fs.readFileSync(`src/i18n/locales/${lang}.json`,'utf8')));
const placeholders=value=>JSON.stringify([...value.matchAll(/{{\s*(\w+)\s*}}/g)].map(m=>m[1]).sort());
for(const [index,dict] of dictionaries.entries()) {
  if(JSON.stringify(Object.keys(dict).sort())!==JSON.stringify(Object.keys(dictionaries[0]).sort()))throw Error(`Key mismatch: ${languages[index]}`);
  for(const [key,value] of Object.entries(dict))if(placeholders(key)!==placeholders(value))throw Error(`Placeholder mismatch: ${languages[index]} ${key}`);
  for(const [key,value] of Object.entries(dict))if(!value.trim())throw Error(`Empty translation: ${languages[index]} ${key}`);
  if(languages[index]==='hi')for(const [key,value] of Object.entries(dict))if(/[\u3400-\u9fff]/.test(value))throw Error(`Untranslated Chinese in Hindi: ${key}`);
}
function scan(dir){for(const entry of fs.readdirSync(dir,{withFileTypes:true})){
 const file=path.join(dir,entry.name);if(entry.isDirectory()){scan(file);continue;}if(!/\.tsx?$/.test(file))continue;
 const source=ts.createSourceFile(file,fs.readFileSync(file,'utf8'),ts.ScriptTarget.Latest,true);
 function visit(node){if(ts.isCallExpression(node)&&node.expression.getText(source)==='t'&&node.arguments[0]&&ts.isStringLiteral(node.arguments[0])){
  const key=node.arguments[0].text;if(!(key in dictionaries[0]))throw Error(`Missing translation: ${file}: ${key}`);
 }ts.forEachChild(node,visit);}visit(source);
}}
scan('src');console.log(`Verified ${Object.keys(dictionaries[0]).length} translations in ${languages.join(', ')}`);
