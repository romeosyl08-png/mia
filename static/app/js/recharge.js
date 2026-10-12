(() => {
  const currencyForm = document.querySelector(".currency-selector");
  const currencySelect = document.getElementById("currencySelect");
  currencySelect?.addEventListener("change", () => currencyForm?.requestSubmit());

  const formatCredits = value => new Intl.NumberFormat("fr-FR").format(value);
  const amountInput = document.getElementById("payment-amount");
  const creditsOutput = document.getElementById("creditsEstimate");

  function updateCredits(){
    if(!amountInput || !creditsOutput || amountInput.disabled) return;
    const amount = Number(amountInput.value);
    const minimum = Number(amountInput.min);
    const ratePerUsd = Number(creditsOutput.dataset.ratePerUsd);
    const creditsPerDollar = Number(creditsOutput.dataset.creditsPerDollar);
    const currency = creditsOutput.dataset.currency;
    const decimals = Number(creditsOutput.dataset.decimals || 2);
    if(!Number.isFinite(amount) || amount < minimum){
      const minimumLabel = new Intl.NumberFormat("fr-FR", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      }).format(minimum);
      creditsOutput.textContent = `Minimum ${minimumLabel} ${currency}`;
      return;
    }
    if(!Number.isFinite(ratePerUsd) || ratePerUsd <= 0) return;
    const estimatedCredits = amount / ratePerUsd * creditsPerDollar;
    creditsOutput.textContent = `${formatCredits(Math.floor(estimatedCredits + 1e-7))} jetons`;
  }

  amountInput?.addEventListener("input", updateCredits);
  updateCredits();

  document.querySelectorAll("[data-set-amount]").forEach(button => {
    button.addEventListener("click", () => {
      const input = document.getElementById(button.dataset.setAmount);
      if(!input || input.disabled) return;
      input.value = button.dataset.amount;
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
  });
})();
