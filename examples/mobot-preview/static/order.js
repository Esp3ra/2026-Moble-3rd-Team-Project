const form = document.querySelector('#configuration');
const assembly = document.querySelector('#assembly');
const slotCount = document.querySelector('#slot-count');
const reset = document.querySelector('#reset-order');
const review = form.querySelector('.review');
const dialog = document.querySelector('#summary-dialog');
const summary = document.querySelector('#summary');
const submitOrder = document.querySelector('#submit-order');
const paymentMethod = form.querySelector('#payment-method');
const paymentBank = form.querySelector('#payment-bank');
const cardIssuer = form.querySelector('#card-issuer');
const useInstallments = form.querySelector('#use-installments');
const installmentMonths = form.querySelector('#installment-months');
const contact = form.querySelector('.order-contact');
const contactFields = [...contact.querySelectorAll('input, textarea')];
const slots = [...form.querySelectorAll('[data-slot]')].map(fieldset => ({
  label: fieldset.querySelector('legend').textContent,
  fieldset,
  mount: assembly.querySelector(`[data-mount="${fieldset.dataset.slot}"]`),
  fallback: fieldset.dataset.slot === 'head' ? '#empty-head' : '#empty-arm',
}));

function selectedOptions() {
  return slots.map(slot => ({ ...slot, choice: slot.fieldset.querySelector('input:checked') }));
}

function optionName(choice) {
  return choice.closest('label').querySelector('.option-name').textContent;
}

function updateReset() {
  reset.disabled = !paymentMethod?.value && !contactFields.some(field => field.value) &&
    !form.querySelector('[data-slot] input:checked');
}

function updatePayment() {
  if (!paymentMethod) return;
  const card = paymentMethod.value === 'card';
  for (const [field, enabled] of [
    [paymentBank, paymentMethod.value === 'transfer'],
    [cardIssuer, card],
    [useInstallments, card],
    [installmentMonths, card && useInstallments.checked],
  ]) {
    field.disabled = !enabled;
    field.hidden = !enabled;
    field.labels[0].hidden = !enabled;
  }
}

function cardMonths() {
  return useInstallments.checked ? installmentMonths.valueAsNumber : 1;
}

function updateAssembly() {
  const selection = selectedOptions();
  const installed = selection.filter(slot => slot.choice).length;
  const description = selection.map(({ choice, label, mount, fallback }) => {
    mount.setAttribute('href', choice ? `#${choice.value}` : fallback);
    return `${label} ${choice ? optionName(choice) : '미장착'}`;
  });
  assembly.setAttribute('aria-label', `MOBOT 정면 조립 모습. ${description.join(', ')}`);
  slotCount.textContent = `${installed} / ${slots.length} 장착`;
  updateReset();
  review.disabled = installed !== slots.length;
}

function clearConfiguration() {
  if (dialog.open) dialog.close();
  form.reset();
  updatePayment();
  summary.replaceChildren();
  updateAssembly();
}

if (submitOrder) {
  const status = document.querySelector('#submission-status');
  const close = dialog.querySelector('button[autofocus]');
  const editLabel = close.textContent;
  let requestId;
  let requestBody;
  let received = false;

  form.addEventListener('reset', () => {
    requestId = undefined;
    requestBody = undefined;
  });

  dialog.addEventListener('close', () => {
    if (received) clearConfiguration();
    received = false;
    close.textContent = editLabel;
    submitOrder.disabled = false;
    status.textContent = '';
  });

  dialog.addEventListener('cancel', event => {
    if (close.disabled) event.preventDefault();
  });

  submitOrder.addEventListener('click', async () => {
    const order = Object.fromEntries(new FormData(form));
    if (order.payment_method === 'card') order.installment_months = cardMonths();
    const body = JSON.stringify(order);
    if (body !== requestBody) {
      requestBody = body;
      const bytes = crypto.getRandomValues(new Uint8Array(16));
      requestId = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    }

    submitOrder.disabled = true;
    close.disabled = true;
    status.textContent = '주문을 접수하고 있습니다.';
    try {
      const response = await fetch('/api/orders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...order, request_id: requestId }),
      });
      const result = await response.json();
      if (!response.ok) {
        status.textContent = result.error || '주문을 다시 접수해 주세요.';
        return;
      }
      received = true;
      status.textContent = `접수되었습니다. 주문 번호 ${result.id}`;
      close.textContent = '닫기';
    } catch {
      status.textContent = '접수 응답을 받지 못했습니다. 주문 접수를 다시 눌러 확인해 주세요.';
    } finally {
      submitOrder.disabled = received;
      close.disabled = false;
      if (received) close.focus();
    }
  });
}

form.addEventListener('change', () => {
  updatePayment();
  updateAssembly();
});
contact.addEventListener('input', updateReset);
reset.addEventListener('click', event => {
  event.preventDefault();
  clearConfiguration();
  form.querySelector('input').focus({ preventScroll: true });
});
form.addEventListener('submit', event => {
  event.preventDefault();
  const selection = selectedOptions();
  if (selection.some(slot => !slot.choice)) return;
  for (const field of contactFields) field.value = field.value.trim();
  if (!form.reportValidity()) return;

  const details = [
    ...contactFields.map(field => [field.labels[0].textContent, field.value]),
    ...selection.map(({ label, choice }) => [label, optionName(choice)]),
  ];
  if (paymentMethod) {
    details.push(['결제 수단', paymentMethod.selectedOptions[0].textContent]);
    if (paymentMethod.value === 'card') {
      details.push(['카드사', cardIssuer.selectedOptions[0].textContent]);
      const months = cardMonths();
      details.push(['할부', months === 1 ? '일시불' : `${months}개월`]);
    } else {
      details.push(['이체 은행', paymentBank.selectedOptions[0].textContent]);
    }
  }
  const rows = details.map(([label, detail]) => {
    const row = document.createElement('div');
    const title = document.createElement('dt');
    const value = document.createElement('dd');
    title.textContent = label;
    value.textContent = detail;
    row.append(title, value);
    return row;
  });
  summary.replaceChildren(...rows);
  dialog.showModal();
});
window.addEventListener('pagehide', clearConfiguration);
window.addEventListener('pageshow', event => {
  if (event.persisted) clearConfiguration();
  else updateAssembly();
});
updatePayment();
updateAssembly();
