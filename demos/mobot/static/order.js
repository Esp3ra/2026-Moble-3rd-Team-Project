const form=document.querySelector('#configuration');
const assembly=document.querySelector('#assembly');
const dialog=document.querySelector('#summary-dialog');
const summary=document.querySelector('#summary');
const submit=document.querySelector('#submit-order');
const review=form.querySelector('.review');
const reset=document.querySelector('#reset-order');
const status=document.querySelector('#submission-status');
const slotCount=document.querySelector('#slot-count');
const fields=['head','left','right'];
let catalog,requestId,requestBody,received=false;
const uuid=()=>{
 if(crypto.randomUUID)return crypto.randomUUID();
 const a=crypto.getRandomValues(new Uint8Array(16));a[6]=(a[6]&15)|64;a[8]=(a[8]&63)|128;
 const h=Array.from(a,x=>x.toString(16).padStart(2,'0')).join('');return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
};
function choice(slot){return form.querySelector(`input[name="${slot}"]:checked`);}
function available(){
 if(!catalog||!catalog.body_available)return false;
 const head=choice('head');if(!head)return false;
 if(!catalog.heads.some(item=>item.id===head.value&&item.available_units>0))return false;
 for(const arm of catalog.arms){
  const need=Number(choice('left')?.value===arm.left_code)+Number(choice('right')?.value===arm.right_code);
  if(need>arm.shared_units)return false;
 }return true;
}
function update(){
 let count=0;
 for(const slot of fields){const input=choice(slot);if(input)count++;
  assembly.querySelector(`[data-mount="${slot}"]`).setAttribute('href',input?`#${input.dataset.symbol}`:slot==='head'?'#empty-head':'#empty-arm');}
 slotCount.textContent=`${count} / 3 장착`;review.disabled=count!==3||!available();reset.disabled=false;
}
async function loadCatalog(){
 try{const response=await fetch('/api/v1/catalog');const data=await response.json();if(!response.ok)throw new Error(data.message);
  catalog=data;
  for(const input of form.querySelectorAll('input[type="radio"]')){
   const head=data.heads.find(item=>item.id===input.value);
   const arm=data.arms.find(item=>item.left_code===input.value||item.right_code===input.value);
   const units=head?head.available_units:arm?.shared_units??0;input.disabled=units===0;
   let label=input.parentElement.querySelector('.stock-count');if(!label){label=document.createElement('small');label.className='stock-count';input.parentElement.append(label);}
   label.textContent=units===0?' 품절':` 재고 ${units}${arm?' (좌우 공용)':''}`;
  }update();
 }catch(error){catalog=null;review.disabled=true;status.textContent='재고 조회 실패 · '+error.message;}
}
form.addEventListener('change',update);
reset.addEventListener('click',event=>{event.preventDefault();form.reset();requestId=requestBody=undefined;update();});
form.addEventListener('submit',event=>{
 event.preventDefault();if(!form.reportValidity()||!available())return;
 const rows=[...['company','phone','address','payment_method'].map(key=>[key,new FormData(form).get(key)]),...fields.map(key=>[key,choice(key).value])];
 summary.replaceChildren(...rows.map(([key,value])=>{const row=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=({company:'회사명',phone:'연락처',address:'주소',payment_method:'결제 방식',head:'머리',left:'왼팔',right:'오른팔'})[key];dd.textContent=({card:'카드',transfer:'계좌이체'})[value]||value;row.append(dt,dd);return row;}));
 dialog.showModal();
});
submit.addEventListener('click',async()=>{
 const raw=Object.fromEntries(new FormData(form));
 const order={company:raw.company.trim(),phone:raw.phone.trim(),address:raw.address.trim(),payment_method:raw.payment_method,
  spec:{target_head_id:raw.head,target_arm_l_id:raw.left,target_arm_r_id:raw.right}};
 const serialized=JSON.stringify(order);if(serialized!==requestBody){requestBody=serialized;requestId=uuid();}
 submit.disabled=true;
 try{const response=await fetch('/api/v1/orders',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...order,request_id:requestId})});
  const result=await response.json();if(!response.ok)throw new Error(result.message||'접수 실패');
  status.textContent=`접수 완료. 주문번호: ${result.order_id} · 정상 HUB ${result.target_station}. 주문번호를 보관하세요.`;
  document.querySelector('#lookup-order-id').value=result.order_id;received=true;await loadCatalog();
 }catch(error){status.textContent=error.message+' · 응답이 불명확하면 같은 요청으로 다시 확인하세요.';}
 finally{submit.disabled=received;}
});
dialog.addEventListener('close',()=>{if(received){form.reset();requestId=requestBody=undefined;received=false;update();}submit.disabled=false;});
const lookupForm=document.querySelector('#lookup-form'),lookupResult=document.querySelector('#lookup-result'),cancel=document.querySelector('#cancel-own-order');
let lookedUp,cancelRequest;
const progress={RECEIVED:'접수',IN_PRODUCTION:'제작 중',INSPECTING:'검사 중',SHIPPING:'배송 중',HUB_ARRIVED:'허브 도착'};
lookupForm.addEventListener('submit',async event=>{
 event.preventDefault();cancel.disabled=true;lookedUp=null;cancelRequest=undefined;
 try{const response=await fetch('/api/v1/orders/lookup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({order_id:document.querySelector('#lookup-order-id').value.trim()})});
  const result=await response.json();if(!response.ok)throw new Error(result.message);lookedUp=result.order_id;
  lookupResult.textContent=`${progress[result.progress]||result.progress} · ${result.outcome} · HUB ${result.target_station}`;
  cancel.disabled=result.progress!=='RECEIVED'||result.outcome!=='OPEN';
 }catch(error){lookupResult.textContent=error.message;}
});
cancel.addEventListener('click',async()=>{
 if(!lookedUp||!confirm('관리자 수락 전 주문을 취소할까요?'))return;cancel.disabled=true;cancelRequest??=uuid();
 try{const response=await fetch('/api/v1/orders/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({order_id:lookedUp,request_id:cancelRequest})});
  const result=await response.json();if(!response.ok)throw new Error(result.message);lookupResult.textContent='취소 완료 · 재고 복구';await loadCatalog();
 }catch(error){lookupResult.textContent=error.message;cancel.disabled=false;}
});
loadCatalog();
