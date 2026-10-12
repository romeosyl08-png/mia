(() => {
const currencyToggleOptions = [...document.querySelectorAll("[data-payment-currency]")];
    const paymentCards = {
      XOF: document.getElementById("paydunyaPayment"),
      USD: document.getElementById("stripePayment"),
    };
    function selectPaymentCurrency(currency){
      if(!paymentCards[currency]) return;
      currencyToggleOptions.forEach(option => {
        const selected = option.dataset.paymentCurrency === currency;
        option.classList.toggle("active", selected);
        option.setAttribute("aria-pressed", String(selected));
      });
      Object.entries(paymentCards).forEach(([cardCurrency, card]) => {
        if(!card) return;
        const selected = cardCurrency === currency;
        card.hidden = !selected;
        card.classList.toggle("active", selected);
      });
    }
    currencyToggleOptions.forEach(option => {
      option.addEventListener("click", () => selectPaymentCurrency(option.dataset.paymentCurrency));
    });

    const formatCredits = value => new Intl.NumberFormat("fr-FR").format(value);
    const stripeAmountInput = document.getElementById("stripe-amount");
    const stripeCreditsOutput = document.getElementById("stripeCreditsEstimate");
    function updateStripeCredits(){
      if(!stripeAmountInput || !stripeCreditsOutput) return;
      const amount = Number(stripeAmountInput.value);
      const creditsPerDollar = Number(stripeCreditsOutput.dataset.creditsPerDollar);
      if(!Number.isFinite(amount) || amount < Number(stripeAmountInput.min)){
        stripeCreditsOutput.textContent = `Minimum ${stripeAmountInput.min} USD`;
        return;
      }
      stripeCreditsOutput.textContent = `${formatCredits(Math.floor(amount * creditsPerDollar))} jetons`;
    }
    stripeAmountInput?.addEventListener("input", updateStripeCredits);
    updateStripeCredits();

    const paydunyaAmountInput = document.getElementById("paydunya-amount");
    const paydunyaCreditsOutput = document.getElementById("paydunyaCreditsEstimate");
    function updatePaydunyaCredits(){
      if(!paydunyaAmountInput || !paydunyaCreditsOutput) return;
      const amount = Number(paydunyaAmountInput.value);
      const xofPerUsd = Number(paydunyaCreditsOutput.dataset.xofPerUsd);
      const creditsPerDollar = Number(paydunyaCreditsOutput.dataset.creditsPerDollar);
      if(!Number.isFinite(xofPerUsd) || xofPerUsd <= 0){
        paydunyaCreditsOutput.textContent = "Indisponible";
        return;
      }
      if(!Number.isFinite(amount) || amount < Number(paydunyaAmountInput.min)){
        paydunyaCreditsOutput.textContent = `Minimum ${new Intl.NumberFormat("fr-FR").format(Number(paydunyaAmountInput.min))} FCFA`;
        return;
      }
      paydunyaCreditsOutput.textContent = `${formatCredits(Math.floor(amount / xofPerUsd * creditsPerDollar))} jetons`;
    }
    paydunyaAmountInput?.addEventListener("input", updatePaydunyaCredits);
    updatePaydunyaCredits();

    document.querySelectorAll("[data-set-amount]").forEach(button => {
      button.addEventListener("click", () => {
        const input = document.getElementById(button.dataset.setAmount);
        if(!input || input.disabled) return;
        input.value = button.dataset.amount;
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
    });
})();
