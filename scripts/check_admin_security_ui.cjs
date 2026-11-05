const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=require('node:path').resolve(__dirname,'..');
const source=fs.readFileSync(root+'/public-site/gas-source/AdminSecurity.html','utf8');
let n=0;
for(const file of ['AdminSecurity.html','Admin.html']){
  const html=fs.readFileSync(root+'/public-site/gas-source/'+file,'utf8');
  for(const match of html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)){new vm.Script(match[1],{filename:file+':script'+(++n)});}
}
assert(source.includes('location.pathname===\'/admin/\'&&location.protocol===\'https:\''));
assert(source.includes('e.isTrusted'));assert(source.includes("api('session')"));
assert(!/\b(?:window|globalThis)\.(?:alert|confirm|prompt)\(/.test(source));
assert(!/\b(?:alert|prompt)\(/.test(source));assert(source.includes('function confirm(text,doIt)'));
assert(source.includes('textContent=text'));assert(!source.includes('localStorage.setItem'));
assert(!/\p{Extended_Pictographic}/u.test(source));
const admin=fs.readFileSync(root+'/public-site/gas-source/Admin.html','utf8');
assert(admin.indexOf("include('AdminSecurity')")<admin.indexOf('function doLogin()'));
assert(admin.includes('window.AdminSecurity.login(window.__secureEnter)'));
assert(admin.includes('if(window.AdminSecurity){window.AdminSecurity.logout(true);return;}'));
console.log(`Admin security UI: ${n} script blocks parse; same-origin, activity, no-secret-storage, no-emoji and compatibility checks pass. Browser layout remains a separate check.`);
