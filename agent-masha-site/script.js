const toast=document.getElementById('toast');
document.querySelectorAll('[data-action="download"]').forEach((button)=>{
  button.addEventListener('click',()=>{
    toast.classList.add('show');
    clearTimeout(window.mashaToastTimer);
    window.mashaToastTimer=setTimeout(()=>toast.classList.remove('show'),2600);
  });
});
