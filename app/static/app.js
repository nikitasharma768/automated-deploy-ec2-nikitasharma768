fetch('/api/message')
  .then(r => r.json())
  .then(data => {
    document.getElementById('message').innerText = data.message;
  })
  .catch(err => {
    document.getElementById('message').innerText = 'Error fetching message';
    console.error(err);
  });
