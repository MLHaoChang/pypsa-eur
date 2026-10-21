// Original vector dioramas for the design preview. No stock images, inference or remote requests.
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const directory=path.join(path.dirname(fileURLToPath(import.meta.url)),'media');
await mkdir(directory,{recursive:true});
const p=(x,z,h=0)=>[480+(x-z)*1.06,150+(x+z)*.45-h];
const points=vertices=>vertices.map(vertex=>p(...vertex).map(n=>n.toFixed(1)).join(',')).join(' ');
const polygon=(vertices,fill,extra='')=>`<polygon points="${points(vertices)}" fill="${fill}" ${extra}/>`;
const line=(vertices,color,width=1,extra='')=>`<polyline points="${points(vertices)}" fill="none" stroke="${color}" stroke-width="${width}" ${extra}/>`;
const ellipse=(x,z,h,rx,ry,fill,extra='')=>{const [a,b]=p(x,z,h);return `<ellipse cx="${a}" cy="${b}" rx="${rx}" ry="${ry}" fill="${fill}" ${extra}/>`;};
const rect=(x,z,w,d,h,fill)=>polygon([[x,z,h],[x+w,z,h],[x+w,z+d,h],[x,z+d,h]],fill);
function box(x,z,w,d,h,top='#8195ac',left='#465973',right='#25354d',base=0){
 return polygon([[x,z,base+h],[x+w,z,base+h],[x+w,z+d,base+h],[x,z+d,base+h]],top)+polygon([[x,z+d,base+h],[x+w,z+d,base+h],[x+w,z+d,base],[x,z+d,base]],left)+polygon([[x+w,z,base+h],[x+w,z+d,base+h],[x+w,z+d,base],[x+w,z,base]],right);
}
function solar(x,z,cols=5,rows=3){let s='';for(let row=0;row<rows;row++)for(let col=0;col<cols;col++){
 const a=x+col*14,b=z+row*22;
 s+=polygon([[a,b,7],[a+12,b,7],[a+12,b+18,13],[a,b+18,13]],'#284b78','stroke="#83aac8" stroke-width=".7"');
 s+=line([[a+6,b,7],[a+6,b+18,13]],'#6687ad',.5)+line([[a,b+9,10],[a+12,b+9,10]],'#6687ad',.5);
}return s;}
function windows(x,z,w,d,h){let s='';for(let i=5;i<w-2;i+=10)s+=polygon([[x+i,z+d,h-10],[x+i+5,z+d,h-10],[x+i+5,z+d,h-16],[x+i,z+d,h-16]],'#a8d8e6','opacity=".85"');for(let i=5;i<d-2;i+=10)s+=polygon([[x+w,z+i,h-10],[x+w,z+i+5,h-10],[x+w,z+i+5,h-16],[x+w,z+i,h-16]],'#648ba6');return s;}
function hall(x,z,w,d,h){let s=ellipse(x+w/2+15,z+d/2+12,0,w*.9,d*.6,'#050d1c','opacity=".3"')+box(x,z,w,d,h)+windows(x,z,w,d,h);
 for(let i=8;i<w-5;i+=7)s+=line([[x+i,z+d,1],[x+i,z+d,h-3]],'#8fa0b5',.5,'opacity=".35"');
 for(let i=8;i<d-8;i+=14)s+=box(x+8,z+i,w-16,7,5,'#a3b2c3','#596c85','#41516a',h);
 s+=line([[x,z+d,h-2],[x+w,z+d,h-2],[x+w,z,h-2]],'#e1edf5',1,'opacity=".6"');return s;}
function tank(x,z,r,h){const [a,b]=p(x,z,0);const top=b-h;return ellipse(x+8,z+8,0,r*1.4,r*.6,'#050d1c','opacity=".4"')+`<path d="M${a-r},${top}v${h}a${r},${r*.45} 0 0 0 ${r*2},0v-${h}Z" fill="url(#steel)"/><ellipse cx="${a}" cy="${top}" rx="${r}" ry="${r*.45}" fill="#bfcedb"/>`+ellipse(x,z, h*.22,r,r*.45,'none',`stroke="#657c95" stroke-width="2"`)+ellipse(x,z,h*.72,r,r*.45,'none',`stroke="#657c95" stroke-width="2"`)+`<path d="M${a-r*.55},${top+3}v${h-5}" stroke="#edf5fa" opacity=".35" stroke-width="3"/>`;}
function tree(x,z,size=16){const [a,b]=p(x,z);return `<ellipse cx="${a+6}" cy="${b+3}" rx="${size*.8}" ry="4" fill="#061722" opacity=".35"/><path d="M${a},${b}v-${size}" stroke="#5e7877" stroke-width="3"/><path d="M${a-size*.65},${b-7}L${a},${b-size*2}L${a+size*.65},${b-7}Z" fill="#315a61"/><path d="M${a},${b-size*2}L${a+size*.65},${b-7}L${a},${b-10}Z" fill="#203e4e"/>`;}
function turbine(x,z,h=110){const [a,b]=p(x,z);const top=b-h;return ellipse(x+20,z+20,0,40,8,'#031323','opacity=".3"')+`<path d="M${a-3},${b}L${a-1},${top}h3l3,${h}Z" fill="url(#steel)"/><g fill="#d9e4ed" transform="translate(${a} ${top})"><path d="M0 0L-5 -45Q-5 -55 -1 -57L3 -6Z"/><path d="M0 0L-5 -45Q-5 -55 -1 -57L3 -6Z" transform="rotate(120)"/><path d="M0 0L-5 -45Q-5 -55 -1 -57L3 -6Z" transform="rotate(240)"/><circle r="4" fill="#c4d7e6"/></g>`;}
function batteries(x,z,n=4){let s='';for(let i=0;i<n;i++){s+=box(x+i*18,z,13,30,18,'#c2cbd5','#798c9f','#53687d');s+=rect(x+i*18+2,z+4,9,1,18.1,'#ff665b');for(let j=5;j<26;j+=4)s+=line([[x+i*18+13,z+j,3],[x+i*18+13,z+j,13]],'#233b52',.7);}return s;}
function house(x,z){return box(x,z,25,22,15,'#a0b4c2','#7d939c','#43586c')+polygon([[x-2,z-2,15],[x+12,z-2,29],[x+27,z-2,15],[x+27,z+24,15],[x+12,z+24,29],[x-2,z+24,15]],'#486375')+polygon([[x+12,z-2,29],[x+27,z-2,15],[x+27,z+24,15],[x+12,z+24,29]],'#294354')+windows(x,z,25,22,19);}
function truck(x,z){let s=box(x,z,15,8,7,'#f6a75d','#ba644a','#7f4443')+box(x+15,z,7,8,9,'#ffbd77','#dc7657','#864948');for(const dx of [3,15])for(const dz of [0,8])s+=ellipse(x+dx,z+dz,1,3,4,'#122234');return s;}
const defs=`<defs><linearGradient id="sky" x2=".3" y2="1"><stop stop-color="#071526"/><stop offset="1" stop-color="#233d55"/></linearGradient><radialGradient id="aura"><stop stop-color="#668bb1" stop-opacity=".3"/><stop offset="1" stop-color="#152339" stop-opacity="0"/></radialGradient><linearGradient id="steel"><stop stop-color="#5d7791"/><stop offset=".35" stop-color="#d3e1eb"/><stop offset=".65" stop-color="#819cb2"/><stop offset="1" stop-color="#334d67"/></linearGradient><linearGradient id="water" x2="1" y2="1"><stop stop-color="#1d627c"/><stop offset="1" stop-color="#0a253f"/></linearGradient><filter id="blur"><feGaussianBlur stdDeviation="18"/></filter><filter id="grain"><feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="3" stitchTiles="stitch"/><feColorMatrix type="saturate" values="0"/><feComponentTransfer><feFuncA type="linear" slope=".055"/></feComponentTransfer><feBlend in="SourceGraphic" mode="soft-light"/></filter></defs>`;
function start(kind){return `<svg xmlns="http://www.w3.org/2000/svg" width="960" height="600" viewBox="0 0 960 600"><title>${kind}</title>${defs}<rect width="960" height="600" fill="url(#sky)"/><ellipse cx="500" cy="280" rx="530" ry="400" fill="url(#aura)"/><ellipse cx="490" cy="490" rx="330" ry="54" fill="#030b18" opacity=".6" filter="url(#blur)"/>`;}
function base(top='#4c6874'){return box(0,0,400,400,20,top,'#243d51','#152d44',-20);}
const road=(x,z,w,d)=>rect(x,z,w,d,.2,'#324a5c');
let s=start('Data center campus — original illustrative scene')+base('#59717b');
s+=road(0,270,400,38)+road(320,0,40,400)+rect(12,12,290,240,0,'#3e596b');
for(let i=10;i<390;i+=24)s+=line([[i,289,1],[i+10,289,1]],'#c3c9ca',1.5);
for(let i=20;i<240;i+=30)s+=line([[330,i,1],[330,i+12,1]],'#a2b6c1',1);
s+=hall(35,25,105,88,44)+hall(160,25,105,88,44)+hall(35,145,105,88,44)+hall(160,145,105,88,44);
s+=solar(18,324,11,2)+batteries(205,328,5)+box(335,52,38,118,5,'#71909e','#3a5169','#2c4056');
for(let i=0;i<5;i++){s+=box(343,60+i*22,20,11,20,'#a0aebd','#5b6f86','#344c66');s+=line([[353,64+i*22,21],[353,64+i*22,31],[375,64+i*22,31]],'#b6d0e0',1.2);}
for(let i=30;i<390;i+=27)s+=tree(i,392,8);
s+=truck(276,280)+line([[8,8,4],[394,8,4],[394,394,4]],'#b4cad3',.9,'opacity=".6"');
await writeFile(path.join(directory,'datacenter.svg'),s+'<rect width="960" height="600" fill="transparent" filter="url(#grain)"/></svg>');
s=start('Island microgrid — original illustrative scene');
s+=box(-10,-10,420,420,15,'url(#water)','#0b2e48','#102b43',-15);
for(let i=0;i<14;i++)s+=line([[i*28,0,0],[i*28+30,12,0]],'#6fb2c6',1,'opacity=".22"');
const island=[[35,120,4],[120,40,4],[265,48,4],[372,130,4],[355,308,4],[265,370,4],[85,330,4],[28,230,4]];
s+=polygon(island,'#637b75','stroke="#a1ada0" stroke-width="8"');
s+=road(120,98,28,205)+road(120,205,190,24)+solar(177,83,7,3);
s+=turbine(84,125,108)+turbine(119,67,96);
s+=hall(175,248,60,48,25)+batteries(246,260,4);
for(const [x,z] of [[75,210],[80,264],[165,170],[240,184],[292,205]])s+=house(x,z);
for(const [x,z] of [[54,170],[53,231],[92,310],[153,326],[285,321],[335,257],[316,150],[250,60]])s+=tree(x,z,13);
s+=line([[200,249,24],[200,230,12],[120,230,12],[120,151,12]],'#ff7f72',2,'opacity=".8"');
s+=rect(322,275,65,15,1,'#869b9f')+box(376,272,18,22,5,'#bfd1db','#7a92a2','#41627d');
await writeFile(path.join(directory,'microgrid.svg'),s+'<rect width="960" height="600" fill="transparent" filter="url(#grain)"/></svg>');
s=start('Hydrogen production hub — original illustrative scene')+base('#5b7480');
s+=road(0,195,400,32)+road(250,0,28,400)+solar(15,20,10,3);
s+=hall(28,115,185,65,35)+hall(35,245,142,65,38)+batteries(35,339,6);
for(const [x,z] of [[305,50],[353,50],[305,113],[353,113]])s+=tank(x,z,25,75);
for(const [x,z] of [[290,270],[335,270],[290,330],[335,330]])s+=tank(x,z,20,52);
for(let i=0;i<8;i++)s+=box(195,246+i*15,33,10,18,'#b7c6d4','#6d849a','#3c556d');
s+=line([[213,150,21],[260,150,21],[260,72,21],[305,72,21]],'#ed7368',4)+line([[217,291,22],[260,291,22],[260,312,22],[290,312,22]],'#a6c9df',4);
for(let i=0;i<5;i++)s+=box(266,30+i*68,3,6,23,'#9fb3c1','#68879d','#4a607b');
for(let i=25;i<390;i+=38)s+=tree(390,i,8);
s+=truck(130,205);
await writeFile(path.join(directory,'hydrogen.svg'),s+'<rect width="960" height="600" fill="transparent" filter="url(#grain)"/></svg>');
s=start('Mine and processing site — original illustrative scene')+base('#687378');
// Nested excavation terraces, with depth represented by shaded concentric benches.
for(let i=0;i<6;i++){const inset=i*17;s+=rect(12+inset,30+inset,240-inset*2,238-inset*2,-i*3,'#'+['657075','586772','4c5e6b','425361','344553','273b4b'][i]);s+=line([[12+inset,268-inset,-i*3],[252-inset,268-inset,-i*3],[252-inset,30+inset,-i*3]],'#a0a6a3',2,'opacity=".6"');}
s+=line([[20,250,2],[35,245,2],[35,70,2],[75,70,-2],[75,218,-2],[210,218,-5],[210,105,-5],[110,105,-8],[110,183,-8],[178,183,-12]],'#a7a89f',8,'stroke-linejoin="round"');
s+=road(264,0,32,400)+road(0,308,400,30)+hall(303,72,65,120,45)+hall(303,232,65,55,28);
s+=line([[205,221,6],[280,190,38],[330,185,45]],'#20384c',16)+line([[205,221,11],[280,190,43],[330,185,50]],'#7990a1',7);
for(const [x,z] of [[240,208],[280,190],[316,187]])s+=box(x,z,3,4,27,'#90a5b7','#668297','#344d68');
s+=solar(15,350,12,2)+batteries(298,344,4)+tank(352,34,17,29)+truck(47,135)+truck(160,315)+truck(276,270);
await writeFile(path.join(directory,'mining.svg'),s+'<rect width="960" height="600" fill="transparent" filter="url(#grain)"/></svg>');
s=start('Staged ammonia process plant — original illustrative scene')+base('#657586');
s+=road(0,240,400,30)+road(260,0,30,400)+solar(20,20,11,3);
s+=hall(30,120,185,80,42)+hall(36,292,120,60,32)+batteries(175,314,4);
for(const [x,z] of [[310,55],[355,55],[310,119],[355,119]])s+=tank(x,z,22,66);
for(const [x,z] of [[305,300],[352,300]])s+=tank(x,z,24,42);
for(let i=0;i<3;i++){s+=tank(233,120+i*48,12,100-i*12);s+=line([[233,120+i*48,101-i*12],[265,120+i*48,101-i*12],[265,170,30]],'#adc2d4',3);}
s+=line([[215,155,25],[272,155,25],[272,70,25],[310,70,25]],'#bf9adf',4)+line([[195,190,20],[272,190,20],[272,300,20],[305,300,20]],'#e8828d',4)+truck(180,249);
await writeFile(path.join(directory,'ammonia.svg'),s+'<rect width="960" height="600" fill="transparent" filter="url(#grain)"/></svg>');
// IPP renewables + BESS (ticket 06, spec §8.4): wind on the left, a PV field, a battery row by the
// substation, and the export line in the accent red leaving the plot to a pylon.
function pylon(x,z,h=70){let s=ellipse(x+6,z+6,0,14,4,'#031323','opacity=".3"');
 s+=line([[x-6,z-6,0],[x,z,h],[x+6,z+6,0]],'#b4c6d4',1.6)+line([[x-6,z+6,0],[x,z,h],[x+6,z-6,0]],'#8ea6b8',1.2);
 for(const k of [.35,.65,.88])s+=line([[x-12*(1-k),z,h*k],[x+12*(1-k),z,h*k]],'#c9d8e3',1.4);return s;}
s=start('Renewables IPP with battery — original illustrative scene')+base('#5a7468');
s+=road(0,300,400,26)+road(250,0,24,300);
for(const [x,z,h] of [[30,330,112],[40,250,104],[55,170,98],[70,90,92]])s+=turbine(x,z,h);
s+=solar(110,120,10,3);
s+=box(292,70,40,34,20,'#a6b5c3','#5d7088','#3a4d66')+batteries(292,130,4);
for(let i=0;i<4;i++)s+=box(298+i*8,76,3,3,34,'#c4d2de','#7d93a8','#4d637c');
s+=line([[312,87,36],[360,60,46],[396,40,52]],'#ff7f72',2.2,'opacity=".9"')+pylon(396,40,52);
for(let i=20;i<240;i+=34)s+=tree(i,392,8);
for(let i=40;i<300;i+=36)s+=tree(392,i+40,8);
s+=line([[8,8,4],[394,8,4],[394,394,4]],'#b4cad3',.9,'opacity=".6"');
await writeFile(path.join(directory,'ipp.svg'),s+'<rect width="960" height="600" fill="transparent" filter="url(#grain)"/></svg>');
process.stdout.write('Created six original industrial SVG dioramas.\n');
