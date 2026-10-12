(function(root){
  "use strict";
  const labels = {
    web_search:"Recherche sur le Web…", fetch_url:"Lecture d’une page Web…",
    list_files:"Consultation des fichiers…", read_file:"Lecture d’un fichier…", read_file_lines:"Lecture d’un fichier…",
    compare_files:"Comparaison des fichiers…", search_code:"Recherche dans les fichiers…",
    write_file:"Écriture d’un fichier…", edit_file:"Modification d’un fichier…", rename_file:"Renommage d’un fichier…",
    delete_file:"Préparation de la suppression…", create_folder:"Création d’un dossier…",
    visualize_chart:"Création du graphique…", email_search:"Recherche d’e-mails…", email_read:"Lecture d’un e-mail…",
    email_create_draft:"Création d’un brouillon…", email_send:"Préparation de l’e-mail…",
    blender_get_scene:"Lecture de la scène Blender…", blender_list_objects:"Lecture des objets Blender…",
    blender_create_primitive:"Préparation de l’action Blender…", run_python:"Exécution du programme…", run_command:"Exécution de la commande…",
    create_document:"Création du document…", extract_document_text:"Lecture du document scanné…",
    drive_search:"Recherche dans Drive…", drive_read:"Lecture dans Drive…", calendar_events:"Consultation du calendrier…",
    github_repositories:"Consultation des dépôts…", github_read_file:"Lecture du dépôt…"
  };

  function statusLabel(event){
    if(event.status !== "running") return "";
    return labels[event.tool] || "Utilisation d’un outil du workspace…";
  }

  function render(container, events){
    if(!Array.isArray(events)) return;
    for(const event of events){
      if(event.type === "visualization"){
        const chart = AI2AICharts.createChart(event.chart);
        if(chart) container.appendChild(chart);
      }else if(event.type === 'artifact'){
        const card = document.createElement('section'); card.className = 'chat-tool-confirmation';
        const name = document.createElement('strong'); name.textContent = event.name || 'Document'; card.append(name);
        for(const [url,label] of [[event.url,'Ouvrir'],[event.download_url,'Télécharger']]){
          if(typeof url !== 'string' || !/^\/bara\/\d+\/(documents\/\d+\/|files\/\d+\/download\/)$/.test(url)) continue;
          const a = document.createElement('a'); a.href = url; a.textContent = label; a.style.marginRight = '12px'; card.append(a);
        }
        container.append(card);
      }else if(event.type === "confirmation"){
        const card = document.createElement("section");
        card.className = "chat-tool-confirmation";
        const heading = document.createElement("strong");
        heading.textContent = "Confirmation nécessaire";
        const preview = document.createElement("p");
        preview.textContent = event.description || "Confirmer cette action dans le workspace.";
        const status = document.createElement("span");
        status.className = "chat-action-status";
        const controls = document.createElement("div");
        controls.className = "chat-action-controls";
        function update(){
          const messages = {pending:"En attente de ta confirmation", running:"Action en cours…", success:"Action effectuée", rejected:"Action refusée", error:"L’action a échoué", unavailable:"Action indisponible"};
          status.textContent = messages[event.status] || "Action traitée";
          controls.hidden = event.status !== "pending";
        }
        for(const [decision, label] of [["confirm", "Confirmer"], ["reject", "Refuser"]]){
          const button = document.createElement("button");
          button.type = "button";
          button.className = decision === "confirm" ? "chat-action-confirm" : "chat-action-reject";
          button.textContent = label;
          button.addEventListener("click", async () => {
            Array.from(controls.children).forEach(control => { control.disabled = true; });
            status.textContent = "Traitement…";
            try{
              const response = await fetch(`/bara/${Number(event.workspace_id)}/actions/${Number(event.action_id)}/${decision}/`, {
                method:"POST", headers:{"X-CSRFToken":document.querySelector("[name=csrfmiddlewaretoken]")?.value || ""}
              });
              const data = await response.json();
              if(!response.ok){
                event.status = response.status === 409 ? "unavailable" : "error";
                update();
                status.textContent = data.error || "L’action n’a pas pu être traitée.";
                return;
              }
              event.status = decision === "reject" ? "rejected" : data.status === "error" ? "error" : "success";
              update();
            }catch(_error){
              status.textContent = "Connexion interrompue. Vérifie le journal du workspace avant de réessayer.";
              event.status = "unavailable";
              controls.hidden = true;
            }
          });
          controls.appendChild(button);
        }
        card.append(heading, preview, status, controls);
        update();
        container.appendChild(card);
      }
    }
  }

  root.AI2AIChatTools = {render, statusLabel};
})(globalThis);
