using System.Text;
using ProxyWorld.wit.imports.wasi.http.v0_2_11;

namespace ProxyWorld.wit.exports.wasi.http.v0_2_11;

public class IncomingHandlerImpl : IIncomingHandler
{
    public static void Handle(ITypes.IncomingRequest request, ITypes.ResponseOutparam responseOut)
    {
        var content = Encoding.ASCII.GetBytes("Hello, World!");
        var headers = new List<(string, byte[])> {
        ("content-type", Encoding.ASCII.GetBytes("text/plain")),
    };
        ITypes.Fields responseHeaders;
        try
        {
            responseHeaders = ITypes.Fields.FromList(headers);
        }
        catch (global::ProxyWorld.WitException<ITypes.HeaderError> ex)
        {
            Console.WriteLine($"DEBUG: Fields.FromList failed with HeaderError tag={ex.TypedValue.Tag}");
            responseHeaders = new ITypes.Fields();
        }
        var response = new ITypes.OutgoingResponse(responseHeaders);
        var body = response.Body();
        ITypes.ResponseOutparam.Set(responseOut, Result<ITypes.OutgoingResponse, ITypes.ErrorCode>.Ok(response));
        using (var stream = body.Write())
        {
            stream.BlockingWriteAndFlush(content);
        }
        ITypes.OutgoingBody.Finish(body, null);
    }
}