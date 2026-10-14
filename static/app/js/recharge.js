(() => {
  const currencyForm = document.querySelector(".currency-selector");
  const currencySelect = document.getElementById("currencySelect");
  currencySelect?.addEventListener("change", () => currencyForm?.requestSubmit());

  const formatCredits = value => new Intl.NumberFormat("fr-FR").format(value);
  const amountInput = document.getElementById("payment-amount");
  const creditsOutput = document.getElementById("creditsEstimate");
  const paymentForm = document.getElementById("paymentForm");
  const submitButton = paymentForm?.querySelector('button[type="submit"]');
  const formHint = document.getElementById("paymentFormHint");
  const fields = ["payment-customer-name", "payment-customer-email", "payment-customer-phone", "payment-amount"]
    .map(id => document.getElementById(id));
  const touched = new Set();

  function updateCredits(){
    if(!amountInput || !creditsOutput || amountInput.disabled) return;
    const amount = Number(amountInput.value);
    const ratePerUsd = Number(creditsOutput.dataset.ratePerUsd);
    const creditsPerDollar = Number(creditsOutput.dataset.creditsPerDollar);
    if(!amountInput.value || !Number.isFinite(amount) || !amountInput.validity.valid){
      creditsOutput.textContent = "— jetons";
      return;
    }
    if(!Number.isFinite(ratePerUsd) || ratePerUsd <= 0) return;
    const estimatedCredits = amount / ratePerUsd * creditsPerDollar;
    creditsOutput.textContent = `${formatCredits(Math.floor(estimatedCredits + 1e-7))} jetons`;
  }

  function validationMessage(field){
    const value = field.value.trim();
    if(field === amountInput){
      const amount = Number(value);
      if(!value || !Number.isFinite(amount) || field.validity.badInput) return "Saisis un montant valide.";
      if(amount < Number(field.min)){
        const minimum = new Intl.NumberFormat("fr-FR").format(Number(field.min));
        return `Le montant minimum est de ${minimum} ${creditsOutput.dataset.currency}.`;
      }
      if(field.validity.stepMismatch){
        return creditsOutput.dataset.currency === "XOF"
          ? "Le montant doit être un nombre entier en FCFA."
          : "Le montant doit comporter au plus deux décimales.";
      }
      return "";
    }
    if(field.id === "payment-customer-name"){
      return !value ? "Renseigne ton nom complet."
        : value.length > field.maxLength ? "Le nom ne doit pas dépasser 100 caractères." : "";
    }
    if(field.id === "payment-customer-email"){
      const domain = value.split("@").pop();
      const validDomain = domain === "localhost" || /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(value);
      return !value || value.length > field.maxLength || field.validity.typeMismatch || !validDomain
        ? "Saisis une adresse e-mail valide, par exemple nom@exemple.com." : "";
    }
    const digits = value.replace(/\D/g, "");
    return !/^\+?[\d\s().-]+$/.test(value) || digits.length < 6 || digits.length > 20 || value.length > field.maxLength
      ? "Saisis un numéro de téléphone valide (6 à 20 chiffres)." : "";
  }

  function validateForm(){
    fields.forEach(field => {
      field.setCustomValidity("");
      const message = validationMessage(field);
      field.setCustomValidity(message);
      const showError = Boolean(message && touched.has(field));
      field.setAttribute("aria-invalid", String(showError));
      const error = document.getElementById(`${field.id}-error`);
      if(error){
        if(error.textContent !== message) error.textContent = message;
        error.hidden = !showError;
      }
      field.closest(".currency-input")?.classList.toggle("is-invalid", showError);
    });
    submitButton.disabled = !fields.every(field => field.validity.valid);
    if(formHint){
      const hint = submitButton.disabled
        ? "Complète ou corrige les champs pour continuer."
        : "Tu peux continuer vers le paiement.";
      if(formHint.textContent !== hint) formHint.textContent = hint;
    }
    updateCredits();
  }

  if(paymentForm && submitButton && fields.every(Boolean)){
    // Keep native validation as the fallback when JavaScript is unavailable.
    paymentForm.noValidate = true;
    fields.forEach(field => {
      const onInput = () => { touched.add(field); validateForm(); };
      field.addEventListener("input", onInput);
      field.addEventListener("change", onInput);
      field.addEventListener("blur", () => {
        if(field !== amountInput) field.value = field.value.trim();
        onInput();
      });
    });
    paymentForm.addEventListener("focusin", validateForm);
    paymentForm.addEventListener("submit", event => {
      fields.forEach(field => {
        touched.add(field);
        if(field !== amountInput) field.value = field.value.trim();
      });
      validateForm();
      if(submitButton.disabled){
        event.preventDefault();
        fields.find(field => !field.validity.valid)?.focus();
      }
    });
    window.addEventListener("pageshow", validateForm);
    validateForm();
  } else {
    amountInput?.addEventListener("input", updateCredits);
    updateCredits();
  }

  document.querySelectorAll("[data-set-amount]").forEach(button => {
    button.addEventListener("click", () => {
      const input = document.getElementById(button.dataset.setAmount);
      if(!input || input.disabled) return;
      input.value = button.dataset.amount;
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
  });
})();
