<form id="ask" class="form-horizontal" action="/ask" method="POST">
<label class="control-label" for="query">Name</label>
    <div class="controls">
        <input type="text" class="input-xlarge focused" size=100 maxlength="10000" id="query" name="query"/>
    </div>
    <div class="modal-footer">
        <input type="hidden" name="ask" value="" />
        <button type="submit" class="btn btn-primary">Search doc2vec</button>
    </div>

</form>

<h3>{{original}}</h3>

<table>
    <tr>
        <th>title</th>
        <th>sentence</th>
        <th>confidence</th>
    </tr>

    % for item in list:
    <tr>
        <td>
            <a href="/download/{{item[0][0]}}">{{item[0][0]}}</a>
        </td>
        <td>
            {{item[0][1]}}
        </td>
        <td>
            {{item[1]}}
        </td>
        </tr>
    % end

</table>
